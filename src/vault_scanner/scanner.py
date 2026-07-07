"""Phase 1 vault scanner — file scanner.

Contract: _system/contracts/03-scan-index-and-conflict-rules.md
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .indexer import Indexer
from .parser import (parse_frontmatter, extract_wikilinks, QuarantineError,
                     validate_frontmatter)


def _compute_rename_fingerprint(content: str) -> str:
    """Compute a rename-stable SHA-256 fingerprint.

    - Strips the 'updated' frontmatter field
    - Strips trailing whitespace from each line
    - Normalizes line endings to LF
    """
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Strip trailing whitespace from every line
    stripped_lines = [line.rstrip() for line in lines]

    # Detect and remove the 'updated' field from frontmatter
    result_lines = []
    in_frontmatter = False
    for line in stripped_lines:
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
            else:
                # Closing delimiter found; pass it through
                result_lines.append(line)
                in_frontmatter = False
                continue
            result_lines.append(line)
            continue

        if in_frontmatter and line.strip().startswith("updated:"):
            continue

        result_lines.append(line)

    normalized = "\n".join(result_lines).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


EXCLUDED_DIRS = {"_system", ".obsidian", ".git", "__pycache__", ".laos"}


def _compute_evidence_hash(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _generate_note_id() -> str:
    return uuid.uuid4().hex


def _is_excluded(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    if not parts:
        return False
    return parts[0] in EXCLUDED_DIRS


def _is_icloud_placeholder(path: Path) -> bool:
    return path.suffix == ".icloud" or path.name.startswith(".") and ".icloud" in path.name


def _is_symlink_escape(path: Path, vault_root: Path) -> bool:
    """Return True if the file is a symlink pointing outside vault_root.

    Symlinks that resolve inside vault_root are allowed.
    """
    if path.is_symlink():
        try:
            real = path.resolve()
            vault_real = vault_root.resolve()
            if not str(real).startswith(str(vault_real)):
                return True
        except (OSError, RuntimeError):
            return True
    return False


def scan_file(path: Path, vault_root: Path, indexer: Indexer) -> dict:
    """Scan a single file and update the index.

    Returns a result dict with status and metadata.
    """
    rel = str(path.relative_to(vault_root))

    if _is_excluded(rel):
        return {"status": "skipped", "reason": "excluded_directory", "relative_path": rel}

    if _is_icloud_placeholder(path):
        return {"status": "skipped", "reason": "icloud_placeholder", "relative_path": rel}

    if _is_symlink_escape(path, vault_root):
        return {"status": "skipped", "reason": "symlink_escape", "relative_path": rel}

    if not path.suffix.lower() == ".md":
        return {"status": "skipped", "reason": "not_markdown", "relative_path": rel}

    stat = path.stat()
    mtime = int(stat.st_mtime)
    size = stat.st_size

    existing = indexer.get_last_state(rel)
    if existing and existing["mtime"] == mtime and existing["size"] == size:
        return {"status": "skipped", "reason": "unchanged", "relative_path": rel}

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Quarantine but preserve previous valid content
        if existing:
            indexer.quarantine(rel, "File is not valid UTF-8")
        return {"status": "quarantined", "reason": "not_valid_utf8", "relative_path": rel}

    evidence_hash = _compute_evidence_hash(content)

    if existing and existing["evidence_hash"] == evidence_hash:
        indexer.update_mtime(rel, mtime, size)
        return {"status": "skipped", "reason": "unchanged", "relative_path": rel}

    try:
        fm, body = parse_frontmatter(content)
    except QuarantineError as exc:
        return {"status": "quarantined", "reason": str(exc), "relative_path": rel}

    # Validate frontmatter against schema rules
    validation_reason = validate_frontmatter(fm)
    if validation_reason:
        if existing:
            indexer.quarantine(rel, validation_reason)
        return {"status": "quarantined", "reason": validation_reason, "relative_path": rel}

    rename_fingerprint = _compute_rename_fingerprint(content)

    # ID priority: frontmatter 'id' field > previously generated ID > new UUID
    note_id = fm.get("id")
    if note_id is None:
        if existing:
            note_id = existing["note_id"]
        else:
            note_id = _generate_note_id()

    links = extract_wikilinks(body)
    indexed_at = datetime.now(timezone.utc).isoformat()

    indexer.upsert(
        note_id=note_id,
        relative_path=rel,
        mtime=mtime,
        size=size,
        evidence_hash=evidence_hash,
        rename_fingerprint=rename_fingerprint,
        indexed_at=indexed_at,
        status="active",
    )

    return {
        "status": "indexed",
        "relative_path": rel,
        "note_id": note_id,
        "evidence_hash": evidence_hash,
        "type": fm.get("type"),
        "links": links,
        "indexed_at": indexed_at,
    }
