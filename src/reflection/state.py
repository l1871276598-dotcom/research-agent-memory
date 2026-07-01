from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _session_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session_id must be a non-empty string")
    return value.strip()


def _event_key(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event_key must be a non-empty string")
    return value.strip()


def _position(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("review_position must be a non-negative integer")
    return value


class ReviewStateStore:
    """SQLite-backed per-session counters for automatic review triggers."""

    def __init__(self, state_dir) -> None:
        self.path = Path(state_dir).expanduser() / "conversation_review.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_state (
                    session_id TEXT PRIMARY KEY,
                    turns INTEGER NOT NULL DEFAULT 0,
                    tool_iterations INTEGER NOT NULL DEFAULT 0,
                    last_reviewed_at TEXT,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(review_state)")
            }
            if "last_memory_review_position" not in columns:
                connection.execute(
                    "ALTER TABLE review_state ADD COLUMN last_memory_review_position INTEGER"
                )
            if "last_skill_review_position" not in columns:
                connection.execute(
                    "ALTER TABLE review_state ADD COLUMN last_skill_review_position INTEGER"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_events (
                    session_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, event_key)
                )
                """
            )
            connection.commit()

    def advance(
        self,
        session_id: str,
        tool_iterations: int = 0,
        turn_increment: int = 1,
        event_key: str | None = None,
    ) -> dict:
        session_id = _session_id(session_id)
        event_key = _event_key(event_key)
        if (
            isinstance(tool_iterations, bool)
            or not isinstance(tool_iterations, int)
            or tool_iterations < 0
        ):
            raise ValueError("tool_iterations must be a non-negative integer")
        if (
            isinstance(turn_increment, bool)
            or not isinstance(turn_increment, int)
            or turn_increment not in {0, 1}
        ):
            raise ValueError("turn_increment must be zero or one")
        now = _timestamp()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if event_key is not None:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO review_events(session_id,event_key,created_at)
                    VALUES(?,?,?)
                    """,
                    (session_id, event_key, now),
                )
                if cursor.rowcount == 0:
                    turn_increment = 0
                    tool_iterations = 0
            connection.execute(
                """
                INSERT INTO review_state(session_id, turns, tool_iterations, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    turns = turns + excluded.turns,
                    tool_iterations = tool_iterations + excluded.tool_iterations,
                    updated_at = excluded.updated_at
                """,
                (session_id, turn_increment, tool_iterations, now),
            )
            connection.commit()
        return self.get(session_id)

    def complete(
        self,
        session_id: str,
        *,
        memory_reviewed: bool,
        skills_reviewed: bool,
        review_position: int | None = None,
    ) -> dict:
        session_id = _session_id(session_id)
        review_position = _position(review_position)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE review_state SET
                    turns = CASE WHEN ? THEN 0 ELSE turns END,
                    tool_iterations = CASE WHEN ? THEN 0 ELSE tool_iterations END,
                    last_memory_review_position = CASE
                        WHEN ? AND ? IS NOT NULL THEN ?
                        ELSE last_memory_review_position
                    END,
                    last_skill_review_position = CASE
                        WHEN ? AND ? IS NOT NULL THEN ?
                        ELSE last_skill_review_position
                    END,
                    last_reviewed_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    int(memory_reviewed),
                    int(skills_reviewed),
                    int(memory_reviewed),
                    review_position,
                    review_position,
                    int(skills_reviewed),
                    review_position,
                    review_position,
                    _timestamp(),
                    _timestamp(),
                    session_id,
                ),
            )
            connection.commit()
        return self.get(session_id)

    def fail(self, session_id: str, error: str) -> dict:
        session_id = _session_id(session_id)
        message = str(error).strip()[:1000] or "review failed"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE review_state SET last_error=?, updated_at=? WHERE session_id=?",
                (message, _timestamp(), session_id),
            )
            connection.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> dict:
        session_id = _session_id(session_id)
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                """
                SELECT turns, tool_iterations, last_reviewed_at, last_error, updated_at,
                       last_memory_review_position, last_skill_review_position
                FROM review_state WHERE session_id=?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return {
                "session_id": session_id,
                "turns": 0,
                "tool_iterations": 0,
                "last_reviewed_at": None,
                "last_error": None,
                "updated_at": None,
                "last_memory_review_position": None,
                "last_skill_review_position": None,
            }
        return {
            "session_id": session_id,
            "turns": row[0],
            "tool_iterations": row[1],
            "last_reviewed_at": row[2],
            "last_error": row[3],
            "updated_at": row[4],
            "last_memory_review_position": row[5],
            "last_skill_review_position": row[6],
        }
