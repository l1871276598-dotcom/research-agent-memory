"""SQLite Kanban coordination selectively adapted from Hermes Agent.

There is no scheduler or Cron. Workers claim tasks explicitly through CAS.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


STATUSES = {"triage", "todo", "ready", "running", "blocked", "review", "done", "archived"}
BLOCK_KINDS = {"dependency", "needs_input", "capability", "transient"}


def _stamp(value=None):
    return (value or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()


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


class KanbanStore:
    def __init__(self, state_dir, board="default"):
        board = _text(board, "board")
        root = Path(state_dir).expanduser() / "kanban" / board
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "kanban.sqlite"
        with closing(_connect(self.path)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project TEXT,
                    priority INTEGER NOT NULL DEFAULT 0,
                    claim_owner TEXT,
                    claim_expires_at TEXT,
                    block_kind TEXT,
                    block_reason TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_links (
                    parent_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    child_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    PRIMARY KEY(parent_id, child_id),
                    CHECK(parent_id <> child_id)
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, priority DESC, created_at);
                """
            )
            connection.commit()

    @staticmethod
    def _event(connection, task_id, event, payload=None):
        connection.execute(
            "INSERT INTO task_events(task_id,event,payload_json,created_at) VALUES(?,?,?,?)",
            (task_id, event, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), _stamp()),
        )

    def create(
        self,
        title,
        description,
        *,
        task_id=None,
        workspace="personal",
        project=None,
        priority=0,
        status="todo",
    ):
        if status not in {"triage", "todo", "ready", "blocked"}:
            raise ValueError("invalid initial task status")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("priority must be an integer")
        task_id = task_id or f"task-{uuid.uuid4().hex[:12]}"
        now = _stamp()
        with closing(_connect(self.path)) as connection:
            connection.execute(
                """
                INSERT INTO tasks(
                    id,title,description,status,workspace,project,priority,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    _text(title, "title"),
                    _text(description, "description"),
                    status,
                    _text(workspace, "workspace"),
                    project,
                    priority,
                    now,
                    now,
                ),
            )
            self._event(connection, task_id, "created", {"status": status})
            connection.commit()
        return self.get(task_id)

    def get(self, task_id):
        task_id = _text(task_id, "task_id")
        with closing(_connect(self.path)) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("task not found")
            parents = [
                item[0]
                for item in connection.execute(
                    "SELECT parent_id FROM task_links WHERE child_id=? ORDER BY parent_id",
                    (task_id,),
                )
            ]
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json")) if value.get("result_json") else None
        value["parents"] = parents
        return value

    def list(self, status=None, limit=100):
        if status is not None and status not in STATUSES:
            raise ValueError("invalid task status")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be positive")
        query = "SELECT id FROM tasks"
        params = []
        if status is not None:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY priority DESC,created_at LIMIT ?"
        params.append(limit)
        with closing(_connect(self.path)) as connection:
            ids = [row[0] for row in connection.execute(query, params)]
        return [self.get(task_id) for task_id in ids]

    def link(self, parent_id, child_id):
        parent_id = _text(parent_id, "parent_id")
        child_id = _text(child_id, "child_id")
        with closing(_connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO task_links(parent_id,child_id) VALUES(?,?)",
                (parent_id, child_id),
            )
            self._event(connection, child_id, "dependency_added", {"parent_id": parent_id})
            connection.commit()
        return self.get(child_id)

    def recompute_ready(self):
        changed = []
        with closing(_connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT t.id FROM tasks t
                WHERE t.status='todo'
                  AND NOT EXISTS (
                    SELECT 1 FROM task_links l
                    JOIN tasks p ON p.id=l.parent_id
                    WHERE l.child_id=t.id AND p.status <> 'done'
                  )
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE tasks SET status='ready',updated_at=? WHERE id=? AND status='todo'",
                    (_stamp(), row["id"]),
                )
                self._event(connection, row["id"], "ready")
                changed.append(row["id"])
            connection.commit()
        return changed

    def claim(self, owner, ttl_seconds=900):
        owner = _text(owner, "owner")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        expires = _stamp(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))
        with closing(_connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM tasks WHERE status='ready' ORDER BY priority DESC,created_at LIMIT 1"
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                UPDATE tasks SET status='running',claim_owner=?,claim_expires_at=?,updated_at=?
                WHERE id=? AND status='ready' AND claim_owner IS NULL
                """,
                (owner, expires, _stamp(), row["id"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            self._event(connection, row["id"], "claimed", {"owner": owner})
            connection.commit()
        return self.get(row["id"])

    def heartbeat(self, task_id, owner, ttl_seconds=900):
        expires = _stamp(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds))
        with closing(_connect(self.path)) as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET claim_expires_at=?,updated_at=?
                WHERE id=? AND status='running' AND claim_owner=?
                """,
                (expires, _stamp(), task_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("task claim is not owned")
            connection.commit()
        return self.get(task_id)

    def complete(self, task_id, owner, result=None):
        with closing(_connect(self.path)) as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET status='done',claim_owner=NULL,claim_expires_at=NULL,
                    result_json=?,updated_at=?
                WHERE id=? AND status='running' AND claim_owner=?
                """,
                (json.dumps(result or {}, ensure_ascii=False, sort_keys=True), _stamp(), task_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("task claim is not owned")
            self._event(connection, task_id, "completed")
            connection.commit()
        self.recompute_ready()
        return self.get(task_id)

    def block(self, task_id, owner, kind, reason):
        if kind not in BLOCK_KINDS:
            raise ValueError("invalid block kind")
        status = "todo" if kind == "dependency" else "ready" if kind == "transient" else "blocked"
        with closing(_connect(self.path)) as connection:
            cursor = connection.execute(
                """
                UPDATE tasks SET status=?,claim_owner=NULL,claim_expires_at=NULL,
                    block_kind=?,block_reason=?,updated_at=?
                WHERE id=? AND status='running' AND claim_owner=?
                """,
                (status, kind, _text(reason, "reason"), _stamp(), task_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("task claim is not owned")
            self._event(connection, task_id, "blocked", {"kind": kind, "status": status})
            connection.commit()
        return self.get(task_id)
