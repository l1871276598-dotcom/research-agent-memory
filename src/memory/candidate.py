import argparse
import re
from pathlib import Path

import memory_distill

from . import (
    CONFIDENCE_CHOICES,
    CONFIDENTIALITY_CHOICES,
    SCOPE_CHOICES,
    TYPE_CHOICES,
    WORKSPACE_CHOICES,
    _require_data_root,
    check_state_dir,
    collect_validated_records,
    default_state_dir,
)


_CONFIDENCE_CHOICES = frozenset(CONFIDENCE_CHOICES)
_CONFIDENTIALITY_CHOICES = frozenset(CONFIDENTIALITY_CHOICES)
_REVIEW_ACTIONS = frozenset(memory_distill.REVIEW_ACTIONS)
_SCOPE_CHOICES = frozenset(SCOPE_CHOICES)
_TYPE_CHOICES = frozenset(TYPE_CHOICES)
_WORKSPACE_CHOICES = frozenset(WORKSPACE_CHOICES)

CANDIDATE_FIELDS = frozenset({
    "type",
    "title",
    "scope",
    "workspace",
    "confidentiality",
    "source",
    "content",
    "action",
    "confidence",
    "target_id",
    "project",
    "context_id",
    "source_id",
    "source_path",
    "source_sha256",
    "evidence",
    "source_refs",
    "relations",
    "tags",
    "status",
})
_REQUIRED = {
    "type": _TYPE_CHOICES,
    "title": None,
    "scope": _SCOPE_CHOICES,
    "workspace": _WORKSPACE_CHOICES,
    "confidentiality": _CONFIDENTIALITY_CHOICES,
    "source": None,
    "content": None,
}
_OPTIONAL_TEXT = ("target_id", "project", "context_id", "source_id")
_LIST_FIELDS = ("evidence", "source_refs", "relations", "tags")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def validate_candidate_values(values):
    if not isinstance(values, dict):
        raise ValueError("candidate values must be an object")
    if not set(values) <= CANDIDATE_FIELDS:
        raise ValueError("candidate values contain unsupported fields")
    if "status" in values and values["status"] != "candidate":
        raise ValueError("candidate creation requires candidate status")
    for field, choices in _REQUIRED.items():
        value = values.get(field)
        if not _nonblank(value) or (choices is not None and value not in choices):
            raise ValueError("invalid candidate values")
    action = values.get("action", "create")
    if not _nonblank(action) or action not in _REVIEW_ACTIONS:
        raise ValueError("invalid candidate values")
    confidence = values.get("confidence", "inferred")
    if not _nonblank(confidence) or confidence not in _CONFIDENCE_CHOICES:
        raise ValueError("invalid candidate values")
    for field in _OPTIONAL_TEXT:
        value = values.get(field)
        if value is not None and not _nonblank(value):
            raise ValueError("invalid candidate values")
    for field in _LIST_FIELDS:
        value = values.get(field)
        if value is not None and (
            not isinstance(value, list) or not all(_nonblank(item) for item in value)
        ):
            raise ValueError("invalid candidate values")
    source_path = values.get("source_path")
    source_sha256 = values.get("source_sha256")
    if (source_path is None) != (source_sha256 is None):
        raise ValueError("invalid candidate values")
    if source_path is not None:
        if (
            not _nonblank(source_path)
            or Path(source_path).is_absolute()
            or ".." in Path(source_path).parts
            or not isinstance(source_sha256, str)
            or _SHA256.fullmatch(source_sha256) is None
        ):
            raise ValueError("invalid candidate values")
    return values


def _validated_source(root, relative_path):
    if relative_path is None:
        return
    current = root
    for part in Path(relative_path).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("invalid candidate source")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("invalid candidate source") from exc
    if not resolved.is_file():
        raise ValueError("invalid candidate source")


def _validated_store(root):
    try:
        _, rows, errors, _ = collect_validated_records(root)
    except Exception as exc:
        raise ValueError("memory store validation failed") from exc
    if errors:
        raise ValueError("memory store validation failed")
    return rows


class CandidateStore:
    def __init__(self, root, state_dir=None, backend=memory_distill):
        self.root = _require_data_root(root)
        self.state_dir, _ = check_state_dir(
            self.root,
            default_state_dir() if state_dir is None else state_dir,
        )
        if not callable(getattr(backend, "apply_candidate", None)):
            raise ValueError("candidate backend is invalid")
        self.backend = backend

    def create(self, values):
        validate_candidate_values(values)
        _validated_source(self.root, values.get("source_path"))
        _validated_store(self.root)
        fields = {
            "root": str(self.root),
            "state_dir": str(self.state_dir),
            "action": values.get("action", "create"),
            "confirmed": False,
            "dry_run": False,
            "max_iterations": 2,
            "confidence": values.get("confidence", "inferred"),
        }
        for name in (
            "type",
            "title",
            "scope",
            "workspace",
            "confidentiality",
            "source",
            "content",
            "target_id",
            "project",
            "context_id",
            "source_id",
            "source_path",
            "source_sha256",
            "evidence",
            "source_refs",
            "relations",
            "tags",
        ):
            fields[name] = values.get(name)
        if fields["source_sha256"] is not None:
            fields["source_sha256"] = fields["source_sha256"].lower()
        backend_failed = False
        try:
            result = self.backend.apply_candidate(argparse.Namespace(**fields))
        except Exception:
            backend_failed = True
        if backend_failed:
            raise ValueError("candidate creation failed")
        if not isinstance(result, dict) or not isinstance(result.get("candidate_id"), str):
            raise ValueError("candidate backend did not create a candidate")
        rows = _validated_store(self.root)
        item = next(
            (row for row in rows if row["record"].get("id") == result["candidate_id"]),
            None,
        )
        if item is None or item["record"].get("status") != "candidate":
            raise ValueError("candidate backend did not create a candidate")
        output = dict(result)
        output["path"] = item["relative_path"]
        output["status"] = "candidate"
        return output


__all__ = ["CANDIDATE_FIELDS", "CandidateStore", "validate_candidate_values"]
