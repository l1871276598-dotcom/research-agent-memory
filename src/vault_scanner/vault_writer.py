"""Phase 2 vault writer — Candidate creation.

Sliver 1+2: safe candidate creation, pending_review state, audit events.

Contract: _system/contracts/02-note-identity-and-frontmatter.md
          _system/contracts/03-scan-index-and-conflict-rules.md
          _system/contracts/04-review-verification-and-context-pack.md
"""

import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


# ── Type → directory mapping (frozen) ──────────────────────────────

# Phase 0 contract (02): these types map automatically.
AUTO_TARGET_DIRS = {
    "project": "1-projects",
    "principle": "2-areas",
    "decision": "2-areas",
    "reference": "3-resources",
    "literature": "3-resources",
    "note": "3-resources",
    "personality": "2-areas",
}

# These types require manual target specification.
MANUAL_TARGET_TYPES = {"meeting", "task", "context"}

# Directories that must NEVER be a promotion target.
FORBIDDEN_TARGET_DIRS = {"_system", "0-inbox/human", "0-inbox/laos-generated"}


CANDIDATES_TABLE = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id       TEXT PRIMARY KEY,
    note_id            TEXT,
    relative_path      TEXT NOT NULL,
    evidence_hash      TEXT NOT NULL,
    candidate_state    TEXT NOT NULL DEFAULT 'pending_review',
    created_at         TEXT NOT NULL,
    generator          TEXT,
    generator_version  TEXT,
    reviewed_hash      TEXT,
    reviewed_at        TEXT,
    reviewed_by        TEXT,
    target_directory   TEXT,
    target_path        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_path ON candidates(relative_path);
"""

AUDIT_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type     TEXT NOT NULL,
    candidate_id   TEXT,
    note_id        TEXT,
    actor          TEXT,
    timestamp      TEXT NOT NULL,
    before_state   TEXT,
    after_state    TEXT,
    candidate_hash TEXT,
    target_path    TEXT,
    target_hash    TEXT,
    reason         TEXT,
    tool_version   TEXT,
    plan_id        TEXT
);
"""

PROMOTION_PLANS_TABLE = """
CREATE TABLE IF NOT EXISTS promotion_plans (
    plan_id                TEXT PRIMARY KEY,
    candidate_id           TEXT NOT NULL,
    expected_candidate_hash TEXT NOT NULL,
    target_path            TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    state                  TEXT DEFAULT 'active'
);
"""

CANDIDATE_DIR = "0-inbox/laos-generated"
TOOL_VERSION = "laos-vault-writer-0.1.0"


def init_candidate_db(db_path: Path):
    """Initialize the candidates, promotion_plans, and audit_events tables."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.executescript(CANDIDATES_TABLE)
        conn.executescript(PROMOTION_PLANS_TABLE)
        conn.executescript(AUDIT_EVENTS_TABLE)
        conn.commit()


def _compute_evidence_hash(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_relative_path(relative_path: str, vault_root: Path) -> str:
    """Validate and normalize a candidate relative path.

    Raises ValueError if path is absolute, contains '..', or would
    escape the vault via symlink.
    """
    p = Path(relative_path)
    if p.is_absolute():
        raise ValueError(f"Absolute path not allowed: {relative_path}")
    if ".." in p.parts:
        raise ValueError(f"Path traversal not allowed: {relative_path}")

    # Resolve the full path within vault and check symlink escape
    full_path = (vault_root / p).resolve()
    vault_real = vault_root.resolve()
    if not str(full_path).startswith(str(vault_real)):
        raise ValueError(f"Path escapes vault via symlink: {relative_path}")

    return relative_path


def _write_atomic(file_path: Path, content: str) -> None:
    """Write content atomically via temp file + rename."""
    tmp = file_path.with_name(f"{file_path.name}.laos-tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(content, encoding="utf-8")
        # fsync the file for crash safety
        fd = os.open(str(tmp), os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        tmp.rename(file_path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def _write_audit_event(db_path: Path, event_type: str, candidate_id: str,
                        note_id: str | None = None, actor: str | None = None,
                        before_state: str | None = None,
                        after_state: str | None = None,
                        candidate_hash: str | None = None,
                        target_path: str | None = None,
                        target_hash: str | None = None,
                        reason: str | None = None):
    """Append an audit event. Raises on failure (audit fail = operation fail)."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "INSERT INTO audit_events "
            "(event_type, candidate_id, note_id, actor, timestamp, "
            "before_state, after_state, candidate_hash, target_path, "
            "target_hash, reason, tool_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_type, candidate_id, note_id, actor, _now_iso(),
             before_state, after_state, candidate_hash, target_path,
             target_hash, reason, TOOL_VERSION),
        )
        conn.commit()


def _generate_candidate_id() -> str:
    return f"cand_{uuid.uuid4().hex}"


def create_candidate(
    vault_root: Path,
    db_path: Path,
    content: str,
    generator: str,
    generator_version: str,
    relative_hint: str | None = None,
) -> dict:
    """Create a candidate in 0-inbox/laos-generated/.

    Args:
        vault_root: Root of the vault (authority directory).
        db_path: Path to the SQLite database.
        content: Markdown content including frontmatter.
        generator: Name of the generator (e.g. 'laos-agent').
        generator_version: Version string of the generator.
        relative_hint: Optional relative path hint within laos-generated/.
                       If None, a unique filename is generated.

    Returns:
        dict with candidate_id, relative_path, evidence_hash, candidate_state.

    Raises:
        ValueError: On invalid path (absolute, traversal, symlink escape).
        FileExistsError: On target path already exists.
        Exception: On write or DB failure (atomic, no partial state).
    """
    # ── 1. Determine target path ────────────────────────────────────
    if relative_hint is not None:
        _validate_relative_path(relative_hint, vault_root)
        rel_path = relative_hint
    else:
        candidate_id = _generate_candidate_id()
        rel_path = f"{CANDIDATE_DIR}/{candidate_id}.md"

    # Double-check it's under laos-generated/
    target_rel = Path(rel_path)
    expected_prefix = Path(CANDIDATE_DIR)
    if target_rel.parts[:len(expected_prefix.parts)] != expected_prefix.parts:
        raise ValueError(
            f"Candidate path must be under {CANDIDATE_DIR}/, got {rel_path}"
        )

    file_path = (vault_root / rel_path).resolve()

    # ── 2. Check existing file ──────────────────────────────────────
    if file_path.exists():
        raise FileExistsError(f"Candidate already exists: {rel_path}")

    # ── 3. Ensure parent dir exists ─────────────────────────────────
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 4. Compute hash before writing ──────────────────────────────
    evidence_hash = _compute_evidence_hash(content)

    # ── 5. Atomic write ─────────────────────────────────────────────
    _write_atomic(file_path, content)

    # ── 6. DB write (candidate + audit in same connection) ──────────
    try:
        candidate_id = _generate_candidate_id()
        candidate_state = "pending_review"
        now = _now_iso()
        note_id = None
        import re
        from .parser import parse_frontmatter
        fm, _ = parse_frontmatter(content)
        note_id = fm.get("id")

        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute(
                "INSERT INTO candidates "
                "(candidate_id, note_id, relative_path, evidence_hash, "
                "candidate_state, created_at, generator, generator_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate_id, note_id, rel_path, evidence_hash,
                 candidate_state, now, generator, generator_version),
            )
            conn.execute(
                "INSERT INTO audit_events "
                "(event_type, candidate_id, note_id, actor, timestamp, "
                "after_state, candidate_hash, tool_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("candidate.created", candidate_id, note_id, generator,
                 now, candidate_state, evidence_hash, TOOL_VERSION),
            )
            conn.commit()
    except Exception:
        # Clean up: remove the written file, re-raise
        if file_path.exists():
            file_path.unlink()
        raise

    return {
        "candidate_id": candidate_id,
        "note_id": note_id,
        "relative_path": rel_path,
        "evidence_hash": evidence_hash,
        "candidate_state": candidate_state,
        "created_at": now,
        "generator": generator,
        "generator_version": generator_version,
    }


# ── Sliver 3: Review Gate ──────────────────────────────────────────


REVIEWABLE_STATES = {"pending_review"}
DECISIONS = {"approve", "reject"}


def _get_candidate(db_path: Path, candidate_id: str) -> dict | None:
    """Fetch a candidate row from the DB."""
    with closing(sqlite3.connect(str(db_path))) as conn:
        row = conn.execute(
            "SELECT candidate_id, note_id, relative_path, evidence_hash, "
            "candidate_state, created_at, generator, generator_version, "
            "reviewed_hash, reviewed_at, reviewed_by, "
            "target_directory, target_path "
            "FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "candidate_id": row[0],
        "note_id": row[1],
        "relative_path": row[2],
        "evidence_hash": row[3],
        "candidate_state": row[4],
        "created_at": row[5],
        "generator": row[6],
        "generator_version": row[7],
        "reviewed_hash": row[8],
        "reviewed_at": row[9],
        "reviewed_by": row[10],
        "target_directory": row[11],
        "target_path": row[12],
    }


def review_candidate(
    db_path: Path,
    candidate_id: str,
    decision: str,
    reviewed_by: str,
    review_comment: str | None = None,
) -> dict:
    """Review a candidate: approve or reject.

    The review binds to the candidate's current evidence_hash.
    Rejected candidates cannot be approved later (no-op).

    Returns the updated candidate dict.
    """
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {DECISIONS}, got {decision!r}")

    cand = _get_candidate(db_path, candidate_id)
    if cand is None:
        raise ValueError(f"Candidate not found: {candidate_id}")

    if cand["candidate_state"] not in REVIEWABLE_STATES:
        # Already reviewed: do nothing, return current state
        return cand

    new_state = "approved" if decision == "approve" else "rejected"
    event_type = "candidate.reviewed" if decision == "approve" else "candidate.rejected"
    now = _now_iso()
    reviewed_hash = cand["evidence_hash"]

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "UPDATE candidates SET candidate_state = ?, reviewed_hash = ?, "
            "reviewed_at = ?, reviewed_by = ? WHERE candidate_id = ?",
            (new_state, reviewed_hash, now, reviewed_by, candidate_id),
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(event_type, candidate_id, note_id, actor, timestamp, "
            "before_state, after_state, candidate_hash, reason, tool_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_type, candidate_id, cand["note_id"], reviewed_by,
             now, cand["candidate_state"], new_state, reviewed_hash,
             review_comment, TOOL_VERSION),
        )
        conn.commit()

    return {
        "candidate_id": candidate_id,
        "note_id": cand["note_id"],
        "relative_path": cand["relative_path"],
        "evidence_hash": reviewed_hash,
        "candidate_state": new_state,
        "decision": decision,
        "reviewed_hash": reviewed_hash,
        "reviewed_at": now,
        "reviewed_by": reviewed_by,
        "review_comment": review_comment,
    }


def invalidate_candidate_if_changed(
    db_path: Path,
    candidate_id: str,
    vault_root: Path | None = None,
) -> dict | None:
    """Check if candidate content changed since review. If so, reset to pending_review.

    This does NOT read the file by default (vault_root needed for file path).
    If vault_root is provided and the file content hash differs from
    the reviewed_hash, the candidate is reset to pending_review.

    Returns the updated candidate dict if invalidated, None if unchanged.
    """
    cand = _get_candidate(db_path, candidate_id)
    if cand is None:
        return None

    if cand["candidate_state"] != "approved":
        return None

    if vault_root is None:
        return None

    file_path = (vault_root / cand["relative_path"]).resolve()
    if not file_path.exists():
        return None

    try:
        current_content = file_path.read_text(encoding="utf-8")
    except Exception:
        return None

    current_hash = _compute_evidence_hash(current_content)
    if current_hash == cand.get("reviewed_hash"):
        return None

    # Content has changed → invalidate the review
    now = _now_iso()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "UPDATE candidates SET candidate_state = ?, reviewed_hash = NULL, "
            "reviewed_at = NULL, reviewed_by = NULL WHERE candidate_id = ?",
            ("pending_review", candidate_id),
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(event_type, candidate_id, note_id, actor, timestamp, "
            "before_state, after_state, candidate_hash, reason, tool_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("candidate.review_invalidated", candidate_id, cand["note_id"],
             "system", now, "approved", "pending_review", current_hash,
             "Candidate content changed after approval", TOOL_VERSION),
        )
        conn.commit()

    return {
        "candidate_id": candidate_id,
        "candidate_state": "pending_review",
        "previous_state": "approved",
        "previous_hash": cand.get("reviewed_hash"),
        "current_hash": current_hash,
    }


# ── Sliver 4: Promotion Plan ───────────────────────────────────────


def _type_from_frontmatter(file_path: Path) -> str | None:
    """Read the 'type' field from a candidate file's frontmatter."""
    from .parser import parse_frontmatter
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return None
    fm, _ = parse_frontmatter(content)
    return fm.get("type")


def _resolve_target_path(doc_type: str, candidate_path: str,
                          manual_target_dir: str | None = None) -> tuple[str, str]:
    """Resolve a candidate's type to a target directory and full target path.

    Returns (target_directory, target_path).
    Raises ValueError if the type or target is invalid.
    """
    if doc_type in AUTO_TARGET_DIRS:
        target_dir = AUTO_TARGET_DIRS[doc_type]
    elif doc_type in MANUAL_TARGET_TYPES:
        if not manual_target_dir:
            raise ValueError(
                f"Type '{doc_type}' requires explicit manual_target_dir"
            )
        target_dir = manual_target_dir
    else:
        raise ValueError(f"Unknown type '{doc_type}' — cannot determine target directory")

    # Validate target_dir is not forbidden
    target_dir_str = str(target_dir)
    for forbidden in FORBIDDEN_TARGET_DIRS:
        if target_dir_str.startswith(forbidden):
            raise ValueError(
                f"Target directory '{target_dir_str}' is forbidden "
                f"(reserved: {', '.join(FORBIDDEN_TARGET_DIRS)})"
            )

    # Build filename from candidate path
    candidate_filename = Path(candidate_path).name

    # Ensure target_dir doesn't have path traversal
    target_path_obj = Path(target_dir)
    if ".." in target_path_obj.parts:
        raise ValueError(f"Target directory contains path traversal: {target_dir}")

    target_path = f"{target_dir}/{candidate_filename}"
    return target_dir_str, target_path


def _generate_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex}"


def plan_promotion(
    db_path: Path,
    candidate_id: str,
    vault_root: Path,
    manual_target_dir: str | None = None,
) -> dict:
    """Create a promotion plan for an approved candidate.

    Does NOT execute the promotion — only validates and plans.

    Steps:
      1. Verify candidate exists and is approved
      2. Read candidate file from disk
      3. Determine target directory from frontmatter type
      4. Check if target path already exists (collision)
      5. Record the plan in the database
      6. Write promotion.planned audit event

    Returns the promotion plan dict.
    """
    cand = _get_candidate(db_path, candidate_id)
    if cand is None:
        raise ValueError(f"Candidate not found: {candidate_id}")

    if cand["candidate_state"] != "approved":
        raise ValueError(
            f"Candidate {candidate_id} is '{cand['candidate_state']}', "
            f"must be 'approved' for promotion"
        )

    # Re-read file to get current content and type
    file_path = (vault_root / cand["relative_path"]).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Candidate file not found: {file_path}")

    doc_type = _type_from_frontmatter(file_path)
    if doc_type is None:
        raise ValueError(f"Could not determine 'type' from candidate file")

    target_dir_str, target_path = _resolve_target_path(
        doc_type, cand["relative_path"], manual_target_dir
    )

    # Check target collision
    target_full = (vault_root / target_path).resolve()
    target_exists = target_full.exists()

    now = _now_iso()
    plan_id = _generate_plan_id()
    expected_candidate_hash = cand.get("reviewed_hash")

    # Write plan record + audit event
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "INSERT INTO promotion_plans "
            "(plan_id, candidate_id, expected_candidate_hash, target_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (plan_id, candidate_id, expected_candidate_hash, target_path, now),
        )
        conn.execute(
            "UPDATE candidates SET target_directory = ?, target_path = ? "
            "WHERE candidate_id = ?",
            (target_dir_str, target_path, candidate_id),
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(event_type, candidate_id, note_id, actor, timestamp, "
            "before_state, after_state, candidate_hash, target_path, "
            "reason, tool_version, plan_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("promotion.planned", candidate_id, cand["note_id"],
             "system", now, "approved", "planned",
             expected_candidate_hash, target_path,
             "Target exists" if target_exists else "Target free",
             TOOL_VERSION, plan_id),
        )
        conn.commit()

    return {
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "note_id": cand["note_id"],
        "candidate_state": cand["candidate_state"],
        "doc_type": doc_type,
        "target_directory": target_dir_str,
        "target_path": target_path,
        "target_exists": target_exists,
        "expected_candidate_hash": expected_candidate_hash,
        "reviewed_hash": cand.get("reviewed_hash"),
        "plan_state": "planned",
        "created_at": now,
    }


# ── Sliver 5: Conflict Detection ────────────────────────────────────


def check_promotion_conflicts(
    db_path: Path,
    candidate_id: str,
    vault_root: Path,
    plan_id: str | None = None,
) -> dict | None:
    """Check for conflicts before promotion.

    Checks:
      1. Candidate is in approved state
      2. Candidate content hash matches reviewed_hash
      3. Target path does not exist
      4. Target path is not a symlink

    If any check fails, candidate_state is set to 'conflicted',
    a promotion.conflicted audit event is appended, and the conflict
    dict is returned.

    Returns None if no conflicts (ready for promotion).
    Returns a conflict dict if any check fails.
    """
    cand = _get_candidate(db_path, candidate_id)
    if cand is None:
        return None

    if cand["candidate_state"] != "approved":
        return None

    now = _now_iso()
    reasons = []

    # ── Check 0: Plan-level hash check ──────────────────────────
    if plan_id is not None:
        with closing(sqlite3.connect(str(db_path))) as conn:
            plan_row = conn.execute(
                "SELECT expected_candidate_hash FROM promotion_plans "
                "WHERE plan_id = ? AND state IN ('active', 'promoting')",
                (plan_id,),
            ).fetchone()
        if plan_row is None:
            reasons.append("plan_not_found")
        else:
            plan_expected_hash = plan_row[0]
            file_path_check = (vault_root / cand["relative_path"]).resolve()
            if file_path_check.exists():
                try:
                    plan_content = file_path_check.read_text(encoding="utf-8")
                except Exception:
                    reasons.append("plan_candidate_unreadable")
                else:
                    plan_current_hash = _compute_evidence_hash(plan_content)
                    if plan_current_hash != plan_expected_hash:
                        reasons.append("candidate_plan_hash_mismatch")

    # ── Check 1: Candidate hash vs reviewed_hash ───────────────────
    file_path = (vault_root / cand["relative_path"]).resolve()
    if not file_path.exists():
        reasons.append("candidate_file_missing")
    else:
        try:
            current_content = file_path.read_text(encoding="utf-8")
        except Exception:
            reasons.append("candidate_unreadable")
        else:
            current_hash = _compute_evidence_hash(current_content)
            reviewed_hash = cand.get("reviewed_hash")
            if current_hash != reviewed_hash:
                reasons.append("candidate_hash_changed")

    # ── Check 2: Target must not exist ────────────────────────────
    target_path = cand.get("target_path")
    if target_path:
        target_full = vault_root / target_path
        # Check symlink BEFORE resolve() — resolve follows symlinks
        is_sym = target_full.is_symlink()
        if is_sym:
            reasons.append("target_symlink")
        target_resolved = target_full.resolve()
        if not is_sym and target_resolved.exists():
            reasons.append("target_exists")

    if not reasons:
        return None

    # ── Conflict: update state, write audit ────────────────────────
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "UPDATE candidates SET candidate_state = ? WHERE candidate_id = ?",
            ("conflicted", candidate_id),
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(event_type, candidate_id, note_id, actor, timestamp, "
            "before_state, after_state, candidate_hash, target_path, "
            "reason, tool_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("promotion.conflicted", candidate_id, cand["note_id"],
             "system", now, "approved", "conflicted",
             cand.get("reviewed_hash"), target_path,
             "; ".join(reasons), TOOL_VERSION),
        )
        conn.commit()

    return {
        "candidate_id": candidate_id,
        "candidate_state": "conflicted",
        "reasons": reasons,
    }


# ── Sliver 6: Atomic Promotion ─────────────────────────────────────


def promote(
    db_path: Path,
    plan_id: str,
    vault_root: Path,
) -> dict:
    """Execute an atomic promotion for an approved candidate.

    Steps:
      1. Atomically claim the plan: active → promoting
         - rowcount == 0: re-read state, raise structured error
           (already_completed / claim_failed / promotion_plan_not_active)
      2. Load candidate, verify approved
      3. Run conflict checks (hash, target, symlink)
         - conflict → plan.state = 'conflicted'
      4. Read candidate file content
      5. Atomic write to target path
      6. DB updates: candidate → active, plan → completed, audit
         - DB failure → plan.state = 'failed', cleanup target file
      7. Remove source candidate file (non-fatal)

    Returns a dict with promotion result.
    """
    # ── 1. Atomic claim: active → promoting ─────────────────────────────
    with closing(sqlite3.connect(str(db_path))) as conn:
        cursor = conn.execute(
            "UPDATE promotion_plans SET state = ? "
            "WHERE plan_id = ? AND state = ?",
            ("promoting", plan_id, "active"),
        )
        claimed = cursor.rowcount
        conn.commit()

    if claimed == 0:
        # Claim failed — re-read current state for a structured error
        with closing(sqlite3.connect(str(db_path))) as conn:
            plan_row = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()

        if plan_row is None:
            raise ValueError(f"Promotion plan not found: {plan_id}")

        current_state = plan_row[0]
        if current_state == "completed":
            raise ValueError(
                f"already_completed: plan {plan_id} is already completed"
            )
        elif current_state == "promoting":
            raise ValueError(
                f"claim_failed: plan {plan_id} is already being promoted by another process"
            )
        else:
            raise ValueError(
                f"promotion_plan_not_active: plan {plan_id} is '{current_state}'"
            )

    # ── 2. Load plan details ────────────────────────────────────────────
    with closing(sqlite3.connect(str(db_path))) as conn:
        plan_row = conn.execute(
            "SELECT plan_id, candidate_id, expected_candidate_hash, "
            "target_path, state FROM promotion_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()

    candidate_id = plan_row[1]
    target_path_str = plan_row[3]

    # ── 3. Load candidate ───────────────────────────────────────────────
    cand = _get_candidate(db_path, candidate_id)
    if cand is None:
        raise ValueError(f"Candidate not found: {candidate_id}")

    if cand["candidate_state"] != "approved":
        raise ValueError(
            f"Candidate {candidate_id} is '{cand['candidate_state']}', "
            f"must be 'approved' for promotion"
        )

    # ── 4. Conflict check ───────────────────────────────────────────────
    conflict = check_promotion_conflicts(
        db_path, candidate_id, vault_root, plan_id=plan_id
    )
    if conflict is not None:
        reasons = "; ".join(conflict.get("reasons", ["unknown_conflict"]))
        # Set plan state to conflicted
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = ? WHERE plan_id = ?",
                ("conflicted", plan_id),
            )
            conn.commit()
        raise ValueError(
            f"Promotion conflicts detected for plan {plan_id}: {reasons}"
        )

    # ── 5. Read candidate file content ──────────────────────────────────
    source_path = (vault_root / cand["relative_path"]).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Candidate file not found: {source_path}")

    try:
        content = source_path.read_text(encoding="utf-8")
    except Exception as e:
        raise IOError(f"Failed to read candidate file: {e}") from e

    # ── 6. Ensure target parent dir exists & atomic write ───────────────
    target_path = (vault_root / target_path_str).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(target_path, content)

    # ── 7. DB updates (candidate + plan + audit in one transaction) ─────
    now = _now_iso()

    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute(
                "UPDATE candidates SET candidate_state = ? WHERE candidate_id = ?",
                ("active", candidate_id),
            )
            conn.execute(
                "UPDATE promotion_plans SET state = ? WHERE plan_id = ?",
                ("completed", plan_id),
            )
            conn.execute(
                "INSERT INTO audit_events "
                "(event_type, candidate_id, note_id, actor, timestamp, "
                "before_state, after_state, candidate_hash, target_path, "
                "tool_version, plan_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("promotion.completed", candidate_id, cand["note_id"],
                 "system", now, "approved", "active",
                 cand.get("reviewed_hash"), target_path_str,
                 TOOL_VERSION, plan_id),
            )
            conn.commit()
    except Exception:
        # DB write failed — rollback the file write, set plan to failed
        if target_path.exists():
            target_path.unlink()
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = ? WHERE plan_id = ?",
                ("failed", plan_id),
            )
            conn.commit()
        raise

    # ── 8. Remove source candidate file ─────────────────────────────────
    try:
        source_path.unlink()
    except Exception:
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute(
                "INSERT INTO audit_events "
                "(event_type, candidate_id, note_id, actor, timestamp, "
                "reason, tool_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("promotion.orphan_source", candidate_id, cand["note_id"],
                 "system", _now_iso(),
                 f"Source file not removed: {cand['relative_path']}",
                 TOOL_VERSION),
            )
            conn.commit()

    return {
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "note_id": cand["note_id"],
        "candidate_state": "active",
        "target_path": target_path_str,
        "promoted_at": now,
    }
