from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


EVENT_TYPES = {"message", "checkpoint"}
MESSAGE_ROLES = {"user", "assistant"}
STATUSES = {"captured", "queued", "processing", "processed", "failed", "rejected"}


def _stamp(value=None):
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        connection.execute("PRAGMA journal_mode=DELETE")
    return connection


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize(event):
    if not isinstance(event, dict):
        raise ValueError("bridge event must be an object")
    allowed = {
        "event_id",
        "event_type",
        "source",
        "account_id",
        "conversation_id",
        "branch_id",
        "message_id",
        "parent_message_id",
        "version",
        "role",
        "content",
        "is_final",
        "captured_at",
        "metadata",
    }
    if not set(event) <= allowed:
        raise ValueError("bridge event has unsupported fields")

    event_type = event.get("event_type", "message")
    if event_type not in EVENT_TYPES:
        raise ValueError("invalid bridge event type")
    event_id = _text(event.get("event_id"), "event_id")
    source = _text(event.get("source"), "source").lower()
    account_id = _text(event.get("account_id"), "account_id")
    conversation_id = _text(event.get("conversation_id"), "conversation_id")
    branch_id = _text(event.get("branch_id", "main"), "branch_id")
    metadata = event.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")

    if event_type == "message":
        message_id = _text(event.get("message_id"), "message_id")
        parent = event.get("parent_message_id")
        if parent is not None:
            parent = _text(parent, "parent_message_id")
        version = event.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        role = event.get("role")
        if role not in MESSAGE_ROLES:
            raise ValueError("bridge message role must be user or assistant")
        content = event.get("content")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        is_final = event.get("is_final", False)
        if not isinstance(is_final, bool):
            raise ValueError("is_final must be boolean")
    else:
        message_id = event_id
        parent = None
        version = 1
        role = None
        content = ""
        is_final = True

    captured_at = event.get("captured_at")
    if captured_at is None:
        captured_at = _stamp()
    else:
        try:
            captured_at = _stamp(datetime.fromisoformat(str(captured_at)))
        except ValueError as exc:
            raise ValueError("captured_at must be an ISO timestamp") from exc

    return {
        "event_id": event_id,
        "event_type": event_type,
        "source": source,
        "account_id": account_id,
        "conversation_id": conversation_id,
        "branch_id": branch_id,
        "message_id": message_id,
        "parent_message_id": parent,
        "version": version,
        "role": role,
        "content": content,
        "content_hash": _hash(content),
        "is_final": is_final,
        "captured_at": captured_at,
        "metadata": dict(metadata),
    }


class BridgeEventInbox:
    """Durable, idempotent inbox for browser-bridge events."""

    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "bridge_events.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(_connect(self.path)) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    parent_message_id TEXT,
                    version INTEGER NOT NULL,
                    role TEXT,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    is_final INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    processed_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(
                        source, account_id, conversation_id, branch_id,
                        event_type, message_id, version
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_events_queue
                    ON bridge_events(status, id);
                CREATE INDEX IF NOT EXISTS idx_bridge_events_parent
                    ON bridge_events(
                        source, account_id, conversation_id, branch_id, message_id
                    );
                """
            )
            connection.commit()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        value = dict(row)
        value["is_final"] = bool(value["is_final"])
        value["metadata"] = json.loads(value.pop("metadata_json"))
        return value

    def get(self, event_id):
        event_id = _text(event_id, "event_id")
        with closing(_connect(self.path)) as connection:
            row = connection.execute(
                "SELECT * FROM bridge_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise ValueError("bridge event not found")
        return self._row(row)

    def ingest(self, event):
        value = _normalize(event)
        now = _stamp()
        status = "queued" if value["is_final"] else "captured"
        with closing(_connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM bridge_events WHERE event_id=?",
                (value["event_id"],),
            ).fetchone()
            if existing is None:
                try:
                    connection.execute(
                        """
                        INSERT INTO bridge_events(
                            event_id,event_type,source,account_id,conversation_id,
                            branch_id,message_id,parent_message_id,version,role,
                            content,content_hash,is_final,status,captured_at,
                            updated_at,metadata_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            value["event_id"],
                            value["event_type"],
                            value["source"],
                            value["account_id"],
                            value["conversation_id"],
                            value["branch_id"],
                            value["message_id"],
                            value["parent_message_id"],
                            value["version"],
                            value["role"],
                            value["content"],
                            value["content_hash"],
                            int(value["is_final"]),
                            status,
                            value["captured_at"],
                            now,
                            json.dumps(value["metadata"], ensure_ascii=False, sort_keys=True),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise ValueError("bridge event identity already exists") from exc
                connection.commit()
                result = self.get(value["event_id"])
                result["ingest_status"] = status
                return result

            current = self._row(existing)
            identity = (
                "event_type",
                "source",
                "account_id",
                "conversation_id",
                "branch_id",
                "message_id",
                "version",
                "role",
                "parent_message_id",
            )
            if any(current[field] != value[field] for field in identity):
                connection.rollback()
                raise ValueError("bridge event identity changed")
            if current["content_hash"] == value["content_hash"] and (
                current["is_final"] or not value["is_final"]
            ):
                connection.rollback()
                current["ingest_status"] = "duplicate"
                return current
            if current["is_final"] or current["status"] in {"processing", "processed", "rejected"}:
                connection.rollback()
                raise ValueError("final bridge event is immutable")

            connection.execute(
                """
                UPDATE bridge_events SET
                    content=?,content_hash=?,is_final=?,status=?,captured_at=?,
                    updated_at=?,metadata_json=?,error=NULL
                WHERE event_id=?
                """,
                (
                    value["content"],
                    value["content_hash"],
                    int(value["is_final"]),
                    status,
                    value["captured_at"],
                    now,
                    json.dumps(value["metadata"], ensure_ascii=False, sort_keys=True),
                    value["event_id"],
                ),
            )
            connection.commit()
        result = self.get(value["event_id"])
        result["ingest_status"] = "updated"
        return result

    def claim_next(self):
        with closing(_connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT e.* FROM bridge_events e
                WHERE e.status='queued'
                  AND (
                    e.event_type <> 'message'
                    OR e.parent_message_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM bridge_events p
                        WHERE p.source=e.source
                          AND p.account_id=e.account_id
                          AND p.conversation_id=e.conversation_id
                          AND p.branch_id=e.branch_id
                          AND p.message_id=e.parent_message_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM bridge_events p
                        WHERE p.source=e.source
                          AND p.account_id=e.account_id
                          AND p.conversation_id=e.conversation_id
                          AND p.branch_id=e.branch_id
                          AND p.message_id=e.parent_message_id
                          AND p.status='processed'
                    )
                  )
                ORDER BY e.id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            cursor = connection.execute(
                """
                UPDATE bridge_events SET status='processing',attempts=attempts+1,
                    updated_at=?,error=NULL
                WHERE id=? AND status='queued'
                """,
                (_stamp(), row["id"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        return self.get(row["event_id"])

    def mark_processed(self, event_id):
        event_id = _text(event_id, "event_id")
        with closing(_connect(self.path)) as connection:
            cursor = connection.execute(
                """
                UPDATE bridge_events SET status='processed',processed_at=?,updated_at=?,error=NULL
                WHERE event_id=? AND status='processing'
                """,
                (_stamp(), _stamp(), event_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("bridge event is not processing")
            connection.commit()
        return self.get(event_id)

    def mark_failed(self, event_id, error, *, retry=True, max_attempts=5):
        current = self.get(event_id)
        if current["status"] != "processing":
            raise ValueError("bridge event is not processing")
        retrying = bool(retry) and current["attempts"] < max_attempts
        status = "queued" if retrying else "failed"
        message = str(error).strip()[:2000] or "projection failed"
        with closing(_connect(self.path)) as connection:
            connection.execute(
                "UPDATE bridge_events SET status=?,error=?,updated_at=? WHERE event_id=?",
                (status, message, _stamp(), event_id),
            )
            connection.commit()
        return self.get(event_id)

    def list(self, status=None, limit=100):
        if status is not None and status not in STATUSES:
            raise ValueError("invalid bridge event status")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        query = "SELECT event_id FROM bridge_events"
        params = []
        if status is not None:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY id LIMIT ?"
        params.append(limit)
        with closing(_connect(self.path)) as connection:
            ids = [row[0] for row in connection.execute(query, params)]
        return [self.get(event_id) for event_id in ids]
