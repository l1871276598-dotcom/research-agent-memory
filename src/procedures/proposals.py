import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ACTIONS = {"create", "update", "add_reference", "add_template", "add_script"}
STATUSES = {"candidate", "accepted", "rejected", "applied", "rolled_back"}
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def validate_proposal(value):
    if not isinstance(value, dict):
        raise ValueError("procedure proposal must be an object")
    allowed = {
        "action",
        "name",
        "summary",
        "content",
        "reason",
        "source_refs",
        "category",
        "target_file",
        "expected_sha256",
        "review_key",
    }
    if not set(value) <= allowed:
        raise ValueError("procedure proposal has unsupported fields")
    action = _text(value.get("action", "update"), "action")
    if action not in ACTIONS:
        raise ValueError("invalid procedure proposal action")
    _text(value.get("name"), "name")
    _text(value.get("summary"), "summary")
    _text(value.get("content"), "content")
    refs = value.get("source_refs")
    if not isinstance(refs, list) or not refs or not all(
        isinstance(item, str) and item.strip() for item in refs
    ):
        raise ValueError("source_refs must contain evidence references")
    for field in ("reason", "category", "target_file", "review_key"):
        if field in value and value[field] is not None:
            _text(value[field], field)
    digest = value.get("expected_sha256")
    if digest is not None and (
        not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
    ):
        raise ValueError("expected_sha256 must be a SHA256 digest")
    return value


class ProcedureProposalStore:
    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "procedure_proposals.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    review_reason TEXT,
                    review_key TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(proposals)")
            }
            if "review_key" not in columns:
                connection.execute("ALTER TABLE proposals ADD COLUMN review_key TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_review_key
                ON proposals(review_key) WHERE review_key IS NOT NULL
                """
            )
            connection.commit()

    def create(self, value):
        validate_proposal(value)
        review_key = value.get("review_key")
        proposal_id = f"procedure-{uuid.uuid4().hex[:12]}"
        now = _timestamp()
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if review_key:
                existing = connection.execute(
                    "SELECT id FROM proposals WHERE review_key=?",
                    (review_key,),
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    return self.get(existing[0])
            try:
                connection.execute(
                    """
                    INSERT INTO proposals(id,status,payload,created_at,review_key)
                    VALUES(?,?,?,?,?)
                    """,
                    (proposal_id, "candidate", payload, now, review_key),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                if review_key:
                    with closing(sqlite3.connect(self.path)) as lookup:
                        existing = lookup.execute(
                            "SELECT id FROM proposals WHERE review_key=?",
                            (review_key,),
                        ).fetchone()
                    if existing is not None:
                        return self.get(existing[0])
                raise
            connection.commit()
        return self.get(proposal_id)

    def get(self, proposal_id):
        proposal_id = _text(proposal_id, "proposal_id")
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT status,payload,created_at,reviewed_at,review_reason FROM proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise ValueError("procedure proposal not found")
        return {
            "proposal_id": proposal_id,
            "status": row[0],
            "proposal": json.loads(row[1]),
            "created_at": row[2],
            "reviewed_at": row[3],
            "review_reason": row[4],
        }

    def list(self, status="candidate"):
        if status not in STATUSES:
            raise ValueError("invalid proposal status")
        with closing(sqlite3.connect(self.path)) as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM proposals WHERE status=? ORDER BY created_at,id",
                    (status,),
                )
            ]
        return [self.get(proposal_id) for proposal_id in ids]

    def review(self, proposal_id, action, reason=None):
        action = _text(action, "action")
        if action not in {"accept", "reject"}:
            raise ValueError("review action must be accept or reject")
        current = self.get(proposal_id)
        if current["status"] != "candidate":
            raise ValueError("only candidate proposals can be reviewed")
        status = "accepted" if action == "accept" else "rejected"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE proposals SET status=?,reviewed_at=?,review_reason=? WHERE id=?",
                (status, _timestamp(), reason, proposal_id),
            )
            connection.commit()
        return self.get(proposal_id)

    def transition(self, proposal_id, expected, status):
        if status not in {"applied", "rolled_back"}:
            raise ValueError("invalid proposal transition")
        current = self.get(proposal_id)
        if current["status"] != expected:
            raise ValueError(f"proposal must be {expected}")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE proposals SET status=? WHERE id=?",
                (status, proposal_id),
            )
            connection.commit()
        return self.get(proposal_id)

    def mark_applied(self, proposal_id):
        return self.transition(proposal_id, "accepted", "applied")

    def mark_rolled_back(self, proposal_id):
        return self.transition(proposal_id, "applied", "rolled_back")
