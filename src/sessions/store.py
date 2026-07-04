"""LAOS session store with SQLite and FTS5.

The storage pattern is selectively adapted from Hermes Agent's hermes_state.py
(MIT License): WAL with fallback, message history, lineage, and bounded FTS.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


MAX_QUERY_CHARS = 2048
_TERMS = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_ROLES = {"system", "user", "assistant", "tool"}


def _stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        connection.execute("PRAGMA journal_mode=DELETE")
    return connection


def _fts_query(value):
    value = _text(value, "query")[:MAX_QUERY_CHARS]
    terms = _TERMS.findall(value)
    if not terms:
        raise ValueError("query contains no searchable terms")
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms[:32])


class SessionStore:
    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "sessions.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(_connect(self.path)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    parent_session_id TEXT REFERENCES sessions(id),
                    source TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project TEXT,
                    title TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    end_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(session_id, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    session_id UNINDEXED,
                    message_id UNINDEXED
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace, project);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, ordinal);
                """
            )
            connection.commit()

    def create(
        self,
        *,
        session_id=None,
        parent_session_id=None,
        source="cli",
        workspace="personal",
        project=None,
        title=None,
        metadata=None,
    ):
        session_id = session_id or str(uuid.uuid4())
        _text(session_id, "session_id")
        _text(source, "source")
        _text(workspace, "workspace")
        if parent_session_id is not None:
            _text(parent_session_id, "parent_session_id")
        now = _stamp()
        with closing(_connect(self.path)) as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    id,parent_session_id,source,workspace,project,title,started_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    parent_session_id,
                    source,
                    workspace,
                    project,
                    title,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.commit()
        return self.get(session_id, include_messages=False)

    def append(self, session_id, role, content, metadata=None):
        session_id = _text(session_id, "session_id")
        if role not in _ROLES:
            raise ValueError("invalid message role")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        with closing(_connect(self.path)) as connection:
            exists = connection.execute(
                "SELECT ended_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("session not found")
            if exists["ended_at"] is not None:
                raise ValueError("session is ended")
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO messages(session_id,ordinal,role,content,created_at,metadata_json)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    session_id,
                    ordinal,
                    role,
                    content,
                    _stamp(),
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO messages_fts(content,session_id,message_id) VALUES(?,?,?)",
                (content, session_id, cursor.lastrowid),
            )
            connection.commit()
        return {"session_id": session_id, "ordinal": ordinal, "role": role}

    def sync_turn(self, payload):
        session_id = _text(payload.get("session_id"), "session_id")
        self.append(session_id, "user", str(payload.get("user", "")))
        self.append(session_id, "assistant", str(payload.get("assistant", "")))
        return self.get(session_id)

    def get(self, session_id, include_messages=True):
        session_id = _text(session_id, "session_id")
        with closing(_connect(self.path)) as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("session not found")
            result = dict(row)
            result["metadata"] = json.loads(result.pop("metadata_json"))
            if include_messages:
                messages = connection.execute(
                    "SELECT ordinal,role,content,created_at,metadata_json FROM messages WHERE session_id=? ORDER BY ordinal",
                    (session_id,),
                ).fetchall()
                result["messages"] = [
                    {
                        "ordinal": item["ordinal"],
                        "role": item["role"],
                        "content": item["content"],
                        "created_at": item["created_at"],
                        "metadata": json.loads(item["metadata_json"]),
                    }
                    for item in messages
                ]
        return result

    def list(self, *, workspace=None, project=None, limit=50):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        clauses = []
        params = []
        if workspace is not None:
            clauses.append("workspace=?")
            params.append(workspace)
        if project is not None:
            clauses.append("project=?")
            params.append(project)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(_connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT id FROM sessions" + where + " ORDER BY started_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self.get(row["id"], include_messages=False) for row in rows]

    def search(self, query, *, workspace=None, project=None, limit=20):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        clauses = ["messages_fts MATCH ?"]
        params = [_fts_query(query)]
        if workspace is not None:
            clauses.append("s.workspace=?")
            params.append(workspace)
        if project is not None:
            clauses.append("s.project=?")
            params.append(project)
        with closing(_connect(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT m.session_id,m.ordinal,m.role,m.content,s.title,s.started_at
                FROM messages_fts
                JOIN messages m ON m.id=messages_fts.message_id
                JOIN sessions s ON s.id=m.session_id
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY bm25(messages_fts),s.started_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def end(self, session_id, reason="completed"):
        session_id = _text(session_id, "session_id")
        reason = _text(reason, "reason")
        with closing(_connect(self.path)) as connection:
            cursor = connection.execute(
                "UPDATE sessions SET ended_at=?,end_reason=? WHERE id=? AND ended_at IS NULL",
                (_stamp(), reason, session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("session not found or already ended")
            connection.commit()
        return self.get(session_id, include_messages=False)

    def branch(self, session_id, *, new_session_id=None, title=None):
        parent = self.get(session_id)
        child = self.create(
            session_id=new_session_id,
            parent_session_id=session_id,
            source=parent["source"],
            workspace=parent["workspace"],
            project=parent["project"],
            title=title or parent["title"],
            metadata={"branched_from": session_id},
        )
        for message in parent["messages"]:
            self.append(
                child["id"],
                message["role"],
                message["content"],
                message["metadata"],
            )
        return self.get(child["id"])
