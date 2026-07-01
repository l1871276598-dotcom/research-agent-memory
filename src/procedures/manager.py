import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SUPPORT_DIRS = {
    "add_reference": "references",
    "add_template": "templates",
    "add_script": "scripts",
}


def _name(value):
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError("invalid procedure name")
    return value


def _safe_target(root, relative):
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("procedure path is redirected")
    resolved = path.resolve(strict=False)
    resolved.relative_to(root.resolve())
    return path


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _document(name, summary, content, sources):
    source_lines = "\n".join(f"- {item}" for item in sources)
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {summary}\n"
        "status: active\n"
        "---\n\n"
        f"{content.strip()}\n\n"
        "## Sources\n"
        f"{source_lines}\n"
    )


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class ProcedureManager:
    def __init__(self, data_root, proposals):
        required = ("get", "mark_applied", "mark_rolled_back")
        if not all(callable(getattr(proposals, name, None)) for name in required):
            raise ValueError("invalid procedure proposal store")
        self.root = Path(data_root).expanduser().resolve(strict=False) / "procedures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.backups = proposals.path.parent / "procedure_backups"
        self.backups.mkdir(parents=True, exist_ok=True)
        self.proposals = proposals

    def _backup_path(self, proposal_id):
        if not isinstance(proposal_id, str) or not proposal_id.startswith("procedure-"):
            raise ValueError("invalid procedure proposal id")
        return self.backups / f"{proposal_id}.json"

    def apply(self, proposal_id):
        record = self.proposals.get(proposal_id)
        if record["status"] != "accepted":
            raise ValueError("procedure proposal is not accepted")
        value = record["proposal"]
        name = _name(value["name"])
        directory = _safe_target(self.root, name)
        document = _safe_target(directory, "PROCEDURE.md")
        action = value["action"]
        target = document

        if action in _SUPPORT_DIRS:
            target_file = value.get("target_file")
            if not isinstance(target_file, str) or not target_file.strip():
                raise ValueError("target_file is required")
            relative = Path(target_file)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
                raise ValueError("target_file must be one safe filename")
            target = _safe_target(directory / _SUPPORT_DIRS[action], relative)

        existed = target.is_file()
        before = target.read_text(encoding="utf-8") if existed else None
        before_hash = _digest(target)

        if action == "create":
            if document.exists():
                raise ValueError("procedure already exists")
            _atomic_write(
                document,
                _document(name, value["summary"], value["content"], value["source_refs"]),
            )
        elif action == "update":
            if not document.is_file() or document.is_symlink():
                raise ValueError("procedure does not exist")
            expected = value.get("expected_sha256")
            if not expected or expected.lower() != before_hash:
                raise ValueError("procedure changed since proposal review")
            _atomic_write(
                document,
                _document(name, value["summary"], value["content"], value["source_refs"]),
            )
        elif action in _SUPPORT_DIRS:
            _atomic_write(target, value["content"])
        else:
            raise ValueError("unsupported procedure action")

        backup = {
            "target": target.relative_to(self.root).as_posix(),
            "existed": existed,
            "content": before,
            "sha256_before": before_hash,
            "sha256_after": _digest(target),
        }
        _atomic_write(
            self._backup_path(proposal_id),
            json.dumps(backup, ensure_ascii=False, sort_keys=True),
        )
        applied = self.proposals.mark_applied(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": applied["status"],
            "path": target.relative_to(self.root).as_posix(),
        }

    def rollback(self, proposal_id):
        record = self.proposals.get(proposal_id)
        if record["status"] != "applied":
            raise ValueError("procedure proposal is not applied")
        backup_path = self._backup_path(proposal_id)
        if not backup_path.is_file() or backup_path.is_symlink():
            raise ValueError("procedure backup is missing")
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        target = _safe_target(self.root, backup["target"])
        if _digest(target) != backup.get("sha256_after"):
            raise ValueError("procedure changed after application")
        if backup["existed"]:
            _atomic_write(target, backup["content"])
        elif target.exists():
            target.unlink()
        rolled_back = self.proposals.mark_rolled_back(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": rolled_back["status"],
            "path": backup["target"],
        }
