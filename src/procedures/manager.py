import hashlib
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


class ProcedureManager:
    def __init__(self, data_root, proposals):
        if not callable(getattr(proposals, "get", None)) or not callable(
            getattr(proposals, "mark_applied", None)
        ):
            raise ValueError("invalid procedure proposal store")
        self.root = Path(data_root).expanduser().resolve(strict=False) / "procedures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.proposals = proposals

    def apply(self, proposal_id):
        record = self.proposals.get(proposal_id)
        if record["status"] != "accepted":
            raise ValueError("procedure proposal is not accepted")
        value = record["proposal"]
        name = _name(value["name"])
        directory = _safe_target(self.root, name)
        document = _safe_target(directory, "PROCEDURE.md")
        action = value["action"]

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
            actual = hashlib.sha256(document.read_bytes()).hexdigest()
            if not expected or expected.lower() != actual:
                raise ValueError("procedure changed since proposal review")
            _atomic_write(
                document,
                _document(name, value["summary"], value["content"], value["source_refs"]),
            )
        elif action in _SUPPORT_DIRS:
            target_file = value.get("target_file")
            if not isinstance(target_file, str) or not target_file.strip():
                raise ValueError("target_file is required")
            relative = Path(target_file)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
                raise ValueError("target_file must be one safe filename")
            target = _safe_target(directory / _SUPPORT_DIRS[action], relative)
            _atomic_write(target, value["content"])
        else:
            raise ValueError("unsupported procedure action")

        applied = self.proposals.mark_applied(proposal_id)
        return {
            "proposal_id": proposal_id,
            "status": applied["status"],
            "path": document.relative_to(self.root).as_posix(),
        }
