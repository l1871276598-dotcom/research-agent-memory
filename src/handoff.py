from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path


_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _validate_project_slug(value):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError("blocked: project_slug_required")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError("blocked: project_slug_required")
    return value


def _validate_sha256(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected_sha256 must be a lowercase SHA256")
    value = value.lower()
    if _SHA256.fullmatch(value) is None:
        raise ValueError("expected_sha256 must be a lowercase SHA256")
    return value


def _project_slug_from_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    for line in lines[1:end]:
        if ":" not in line or line.startswith(" "):
            continue
        key, raw = line.split(":", 1)
        if key.strip() != "project_slug":
            continue
        raw = raw.strip()
        if raw.startswith('"'):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, str) else None
        return raw or None
    return None


def _reject_symlink_ancestors(root, path):
    root = root.resolve(strict=True)
    if root.is_symlink():
        raise ValueError("invalid handoff root")
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("invalid handoff path") from exc
    current = root
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("invalid handoff path")


def _require_source_backed_project(root, project_slug, workspace):
    from memory import collect_validated_records

    _, records, errors, _ = collect_validated_records(root)
    if errors:
        raise ValueError("handoff project validation failed")
    for item in records:
        record = item["record"]
        if (
            record.get("type") == "project"
            and record.get("status") == "active"
            and record.get("project") == project_slug
        ):
            if record.get("workspace") == workspace:
                return
            raise ValueError("blocked: project_not_in_workspace")
    raise ValueError("blocked: project_slug_not_source_backed")


def _render_handoff(project_slug, content):
    return "---\nhandoff_schema: \"laos-handoff/v1\"\nproject_slug: " + json.dumps(project_slug, ensure_ascii=False) + "\n---\n\n" + content.rstrip() + "\n"


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}-{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def update_project_handoff(root, project_slug, content, expected_sha256=None, *, workspace):
    project_slug = _validate_project_slug(project_slug)
    expected_sha256 = _validate_sha256(expected_sha256)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("handoff content must be a non-empty string")
    if workspace not in {"personal", "work"}:
        raise ValueError("workspace must be personal or work")

    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid handoff root")
    if (root / "LAOS_HANDOFF.md").is_symlink():
        raise ValueError("invalid legacy handoff")
    _require_source_backed_project(root, project_slug, workspace)

    target_dir = root / "projects" / project_slug
    staging_dir = root / "_staging" / project_slug
    target = target_dir / "handoff.md"
    staged = staging_dir / "handoff.md"
    previous_sha256 = None
    _reject_symlink_ancestors(root, target_dir)
    _reject_symlink_ancestors(root, staging_dir)

    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ValueError("invalid handoff target")
        raw = target.read_bytes()
        previous_sha256 = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and previous_sha256 != expected_sha256:
            raise ValueError("handoff target sha256 mismatch")
        try:
            existing_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid handoff target") from exc
        if _project_slug_from_frontmatter(existing_text) != project_slug:
            raise ValueError("blocked: target_handoff_belongs_to_another_project")
    elif expected_sha256 is not None:
        raise ValueError("handoff target sha256 mismatch")

    rendered = _render_handoff(project_slug, content)
    if _project_slug_from_frontmatter(rendered) != project_slug:
        raise ValueError("handoff validation failed")
    _write_atomic(staged, rendered)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staged, target)
    except Exception:
        staged.unlink(missing_ok=True)
        raise

    return {
        "project_slug": project_slug,
        "path": f"projects/{project_slug}/handoff.md",
        "status": "updated" if previous_sha256 else "created",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "previous_sha256": previous_sha256,
    }


__all__ = ["update_project_handoff"]
