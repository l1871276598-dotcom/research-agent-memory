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
            connection.commit()

    def advance(
        self,
        session_id: str,
        tool_iterations: int = 0,
        turn_increment: int = 1,
    ) -> dict:
        session_id = _session_id(session_id)
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
    ) -> dict:
        session_id = _session_id(session_id)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                UPDATE review_state SET
                    turns = CASE WHEN ? THEN 0 ELSE turns END,
                    tool_iterations = CASE WHEN ? THEN 0 ELSE tool_iterations END,
                    last_reviewed_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    int(memory_reviewed),
                    int(skills_reviewed),
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
                SELECT turns, tool_iterations, last_reviewed_at, last_error, updated_at
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
            }
        return {
            "session_id": session_id,
            "turns": row[0],
            "tool_iterations": row[1],
            "last_reviewed_at": row[2],
            "last_error": row[3],
            "updated_at": row[4],
        }
