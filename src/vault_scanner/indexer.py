"""Phase 1 vault scanner -- SQLite indexer.

Contract: _system/contracts/03-scan-index-and-conflict-rules.md
"""

import sqlite3
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

# Parser version constant -- matches LAOS vault scanner release
PARSER_VERSION = "laos-vault-scanner-0.1.0"


SCHEMA = """
CREATE TABLE IF NOT EXISTS index_manifest (
    rowid              INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id            TEXT NOT NULL,
    relative_path      TEXT NOT NULL,
    mtime              INTEGER,
    size               INTEGER,
    evidence_hash      TEXT NOT NULL,
    last_indexed_hash  TEXT,
    rename_fingerprint TEXT,
    parser_version     TEXT,
    schema_version     INTEGER,
    indexed_at         TEXT,
    status             TEXT DEFAULT 'active',
    quarantine_reason  TEXT,
    record_state       TEXT DEFAULT 'active',
    verification_state TEXT DEFAULT 'pending'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_path ON index_manifest(relative_path);
CREATE INDEX IF NOT EXISTS idx_manifest_note_id ON index_manifest(note_id);
CREATE INDEX IF NOT EXISTS idx_manifest_status ON index_manifest(status);
"""


class Indexer:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def init_db(self):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def get_last_state(self, relative_path: str) -> dict | None:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT note_id, relative_path, mtime, size, evidence_hash, "
                "last_indexed_hash, status, quarantine_reason "
                "FROM index_manifest WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
        if row is None:
            return None
        return {
            "note_id": row[0],
            "relative_path": row[1],
            "mtime": row[2],
            "size": row[3],
            "evidence_hash": row[4],
            "last_indexed_hash": row[5],
            "status": row[6],
            "quarantine_reason": row[7],
        }

    def upsert(self, note_id: str, relative_path: str, mtime: int, size: int,
               evidence_hash: str, indexed_at: str, status: str = "active",
               rename_fingerprint: str | None = None):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            # Upsert by relative_path: if a row with this path already exists,
            # update it; otherwise insert a new row.
            existing = conn.execute(
                "SELECT rowid FROM index_manifest WHERE relative_path = ?",
                (relative_path,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE index_manifest SET note_id = ?, mtime = ?, size = ?, "
                    "evidence_hash = ?, last_indexed_hash = ?, "
                    "rename_fingerprint = ?, parser_version = ?, "
                    "schema_version = ?, indexed_at = ?, status = ?, "
                    "record_state = ?, verification_state = ? "
                    "WHERE relative_path = ?",
                    (note_id, mtime, size, evidence_hash, evidence_hash,
                     rename_fingerprint, PARSER_VERSION, 1, indexed_at,
                     status, status, "pending", relative_path),
                )
            else:
                conn.execute(
                    "INSERT INTO index_manifest "
                    "(note_id, relative_path, mtime, size, evidence_hash, "
                    "last_indexed_hash, rename_fingerprint, parser_version, "
                    "schema_version, indexed_at, status, record_state, "
                    "verification_state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (note_id, relative_path, mtime, size, evidence_hash,
                     evidence_hash, rename_fingerprint, PARSER_VERSION, 1,
                     indexed_at, status, status, "pending"),
                )
            conn.commit()

    def quarantine(self, relative_path: str, reason: str):
        """Set record_state to quarantined without overwriting content-derived fields.

        This preserves evidence_hash, last_indexed_hash, and other
        content-derived fields from a valid previous index entry.
        """
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE index_manifest SET status = ?, record_state = ?, "
                "quarantine_reason = ?, indexed_at = ? WHERE relative_path = ?",
                ("quarantined", "quarantined", reason, self._now_iso(), relative_path),
            )
            conn.commit()

    def update_mtime(self, relative_path: str, mtime: int, size: int):
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE index_manifest SET mtime = ?, size = ?, indexed_at = ? "
                "WHERE relative_path = ?",
                (mtime, size, self._now_iso(), relative_path),
            )
            conn.commit()

    def get_all_active(self) -> list[dict]:
        """Return all manifest entries with status='active'."""
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            rows = conn.execute(
                "SELECT note_id, relative_path, mtime, size, evidence_hash, "
                "last_indexed_hash, rename_fingerprint, status, quarantine_reason "
                "FROM index_manifest WHERE status = 'active'",
            ).fetchall()
        return [
            {
                "note_id": row[0],
                "relative_path": row[1],
                "mtime": row[2],
                "size": row[3],
                "evidence_hash": row[4],
                "last_indexed_hash": row[5],
                "rename_fingerprint": row[6],
                "status": row[7],
                "quarantine_reason": row[8],
            }
            for row in rows
        ]

    def update_status(self, relative_path: str, status: str):
        """Update the status of an entry by relative_path."""
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE index_manifest SET status = ? WHERE relative_path = ?",
                (status, relative_path),
            )
            conn.commit()

    def update_path(self, old_path: str, new_path: str):
        """Update the relative_path of an entry."""
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE index_manifest SET relative_path = ? WHERE relative_path = ?",
                (new_path, old_path),
            )
            conn.commit()

    def delete_entry(self, relative_path: str):
        """Delete an entry by relative_path."""
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "DELETE FROM index_manifest WHERE relative_path = ?",
                (relative_path,),
            )
            conn.commit()

    def merge_onto_path(self, old_path: str, new_path: str, mtime: int, size: int,
                        evidence_hash: str, indexed_at: str):
        """Merge fields from a new scan onto the old-path entry, then move it to new_path.

        Used by fingerprint rename: the old entry retains its note_id and
        rename_fingerprint but adopts the new file's content-derived fields.
        """
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            # Delete the duplicate row at new_path (created by scan_file)
            conn.execute(
                "DELETE FROM index_manifest WHERE relative_path = ?",
                (new_path,),
            )
            # Move the old entry to the new path with updated content fields
            conn.execute(
                "UPDATE index_manifest SET relative_path = ?, mtime = ?, size = ?, "
                "evidence_hash = ?, last_indexed_hash = ?, indexed_at = ? "
                "WHERE relative_path = ?",
                (new_path, mtime, size, evidence_hash, evidence_hash, indexed_at, old_path),
            )
            conn.commit()


def export_receipt_records(indexer: Indexer) -> list[dict]:
    """Query the manifest and return a list of dicts with all receipt fields.

    Proves the fields exist and can be composed into a receipt.
    """
    with closing(sqlite3.connect(str(indexer.db_path))) as conn:
        rows = conn.execute(
            "SELECT note_id, relative_path, evidence_hash, rename_fingerprint, "
            "parser_version, schema_version, record_state, verification_state, "
            "quarantine_reason, indexed_at "
            "FROM index_manifest "
            "ORDER BY relative_path"
        ).fetchall()
    return [
        {
            "note_id": row[0],
            "relative_path": row[1],
            "evidence_hash": row[2],
            "rename_fingerprint": row[3],
            "parser_version": row[4],
            "schema_version": row[5],
            "record_state": row[6],
            "verification_state": row[7],
            "quarantine_reason": row[8],
            "indexed_at": row[9],
        }
        for row in rows
    ]
