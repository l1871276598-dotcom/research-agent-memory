"""Decision-bound memory activation authority.

This module closes the legacy ``action + candidate_id`` promotion bypass by
splitting review into two immutable operations:

* publish a decision bound to one exact candidate snapshot; and
* activate only by decision id plus the expected active-set generation.

The implementation is intentionally local and fail-closed.  It does not claim
biological human-presence verification; a production presence verifier remains
an independent gate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .source_refs import SourceRefError, verify_source_bindings

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported implementation target is POSIX.
    fcntl = None


DECISION_ACTIONS = frozenset({"accept", "reject", "merge", "conflict"})
_DECISION_ID = re.compile(r"mdec_[0-9a-f]{64}\Z")
_ACTIVATION_ID = re.compile(r"mact_[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_AUTHORITY_BYTES = 8 * 1024 * 1024
_CONFIDENTIALITY_RANK = {
    "public": 0,
    "personal": 1,
    "internal": 2,
    "restricted": 3,
}
_REVIEWER_CONFIDENTIALITY = frozenset({"personal", "internal", "restricted"})


class AuthorityError(ValueError):
    """Fail-closed authority error with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReviewerProfile:
    """Immutable reviewer authorization captured from execution context."""

    workspace: str
    confidentiality: str

    def __post_init__(self):
        if self.workspace not in {"personal", "work"}:
            raise AuthorityError("invalid_workspace", "reviewer workspace is invalid")
        if self.confidentiality not in _REVIEWER_CONFIDENTIALITY:
            raise AuthorityError(
                "invalid_confidentiality",
                "reviewer confidentiality authorization is invalid",
            )

    def authorizes(self, workspace: str, confidentiality: str) -> bool:
        return (
            workspace == self.workspace
            and confidentiality in _CONFIDENTIALITY_RANK
            and _CONFIDENTIALITY_RANK[confidentiality]
            <= _CONFIDENTIALITY_RANK[self.confidentiality]
        )

    def semantics(self) -> dict:
        return {
            "workspace": self.workspace,
            "confidentiality": self.confidentiality,
        }


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _nonblank(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _decode_canonical(raw: bytes, *, name: str):
    if not isinstance(raw, bytes) or len(raw) > _MAX_AUTHORITY_BYTES:
        raise AuthorityError("malformed_authority_artifact", f"invalid {name}")

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def no_constants(value):
        raise ValueError(f"invalid constant {value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=no_constants,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthorityError("malformed_authority_artifact", f"invalid {name}") from exc
    try:
        expected = canonical_bytes(parsed)
    except (TypeError, ValueError) as exc:
        raise AuthorityError("malformed_authority_artifact", f"invalid {name}") from exc
    if expected != raw:
        raise AuthorityError("malformed_authority_artifact", f"non-canonical {name}")
    return parsed


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_regular_bytes(path: Path, *, root: Path | None = None) -> bytes:
    """Read one regular file through one no-follow descriptor.

    The opened object, the pre/post path identity, size and mode must agree.
    This prevents a path validation read followed by a different parse read.
    """

    if root is not None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AuthorityError("unsafe_path", "authority path escapes root") from exc
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            try:
                info = os.lstat(current)
            except OSError as exc:
                raise AuthorityError("path_read_failed", "authority path unavailable") from exc
            if stat.S_ISLNK(info.st_mode):
                raise AuthorityError("unsafe_path", "authority path contains a symlink")

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise AuthorityError("path_read_failed", "authority file unavailable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise AuthorityError("unsafe_path", "authority file is not regular")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AuthorityError("path_read_failed", "authority file open failed") from exc
    try:
        opened_before = os.fstat(fd)
        if not stat.S_ISREG(opened_before.st_mode):
            raise AuthorityError("unsafe_path", "authority file is not regular")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, _MAX_AUTHORITY_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_AUTHORITY_BYTES:
                raise AuthorityError("artifact_too_large", "authority file is too large")
        opened_after = os.fstat(fd)
    finally:
        os.close(fd)

    try:
        after = os.lstat(path)
    except OSError as exc:
        raise AuthorityError("path_read_failed", "authority path changed during read") from exc

    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)),
    )
    identity_opened_before = (
        opened_before.st_dev,
        opened_before.st_ino,
        opened_before.st_mode,
        opened_before.st_size,
        getattr(
            opened_before,
            "st_mtime_ns",
            int(opened_before.st_mtime * 1_000_000_000),
        ),
    )
    identity_opened_after = (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_mode,
        opened_after.st_size,
        getattr(
            opened_after,
            "st_mtime_ns",
            int(opened_after.st_mtime * 1_000_000_000),
        ),
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)),
    )
    if not (
        identity_before
        == identity_opened_before
        == identity_opened_after
        == identity_after
    ):
        raise AuthorityError("concurrent_change", "authority file changed during read")
    return b"".join(chunks)


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise AuthorityError("authority_store_unavailable", "authority directory unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AuthorityError("unsafe_path", "authority directory is invalid")


def _write_once(path: Path, payload) -> bool:
    raw = canonical_bytes(payload)
    if path.exists():
        existing = _safe_regular_bytes(path)
        if existing != raw:
            raise AuthorityError("authority_artifact_conflict", "authority artifact conflicts")
        return False

    _ensure_directory(path.parent)
    temp = path.parent / f".tmp.{path.name}.{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(temp, flags, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = None
        try:
            os.link(temp, path)
        except FileExistsError:
            existing = _safe_regular_bytes(path)
            if existing != raw:
                raise AuthorityError(
                    "authority_artifact_conflict", "authority artifact conflicts"
                )
            return False
        _fsync_directory(path.parent)
        return True
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError("authority_write_failed", "authority write failed") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _replace_canonical(path: Path, payload) -> None:
    raw = canonical_bytes(payload)
    _ensure_directory(path.parent)
    temp = path.parent / f".tmp.{path.name}.{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(temp, flags, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise AuthorityError("authority_write_failed", "authority write failed") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


class AuthorityStore:
    """Persistent immutable decisions, activations and active-set generation."""

    def __init__(self, root, state_dir):
        from memory import _require_data_root, check_state_dir

        self.root = _require_data_root(root)
        if state_dir is None:
            raise AuthorityError("state_dir_required", "state_dir is required")
        self.state_dir, _ = check_state_dir(self.root, state_dir)
        self.base = self.state_dir / "memory_authority"
        self.decisions = self.base / "decisions"
        self.activations = self.base / "activations"
        self.pending = self.base / "pending"
        self.generation_path = self.base / "generation.json"
        self.baseline_path = self.base / "baseline_active.json"
        self.lock_path = self.base / "activation.lock"
        for directory in (self.base, self.decisions, self.activations, self.pending):
            _ensure_directory(directory)
        if not self.generation_path.exists():
            _write_once(
                self.generation_path,
                {"schema_version": 1, "active_generation": 0},
            )
        self._ensure_baseline()

    @contextmanager
    def locked(self):
        if fcntl is None:
            raise AuthorityError("lock_unavailable", "activation lock is unavailable")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise AuthorityError("lock_unavailable", "activation lock is unavailable") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _rows(self):
        from memory import collect_validated_records

        _, rows, errors, _ = collect_validated_records(self.root)
        if errors:
            raise AuthorityError("memory_store_invalid", "memory store validation failed")
        return rows

    def _row(self, memory_id: str):
        matches = [row for row in self._rows() if row["record"].get("id") == memory_id]
        if len(matches) != 1:
            raise AuthorityError("candidate_not_found", "candidate not found")
        return matches[0]

    def _record_snapshot(
        self,
        memory_id: str,
        *,
        require_candidate: bool,
        reviewer_profile: ReviewerProfile | None = None,
        verify_provenance: bool = True,
    ):
        if not _nonblank(memory_id):
            raise AuthorityError("invalid_candidate_id", "candidate_id must be non-empty")
        if reviewer_profile is not None and not isinstance(
            reviewer_profile, ReviewerProfile
        ):
            raise AuthorityError("invalid_reviewer_profile", "reviewer profile is invalid")
        row = self._row(memory_id.strip())
        record = copy.deepcopy(row["record"])
        workspace = record.get("workspace")
        confidentiality = record.get("confidentiality")
        if reviewer_profile is not None and not reviewer_profile.authorizes(
            workspace, confidentiality
        ):
            raise AuthorityError("candidate_not_found", "candidate not found")

        status_value = record.get("status")
        if status_value == "conflict":
            status_value = "conflicted"
            record["status"] = status_value
        if require_candidate and status_value != "candidate":
            raise AuthorityError("candidate_not_reviewable", "candidate is not reviewable")
        path = row["path"]
        raw = _safe_regular_bytes(path, root=self.root)
        content = record.get("content")
        if not isinstance(content, str):
            raise AuthorityError("candidate_invalid", "candidate content is invalid")

        if workspace not in {"personal", "work"}:
            raise AuthorityError("partition_invalid", "candidate workspace is invalid")
        if confidentiality not in _CONFIDENTIALITY_RANK:
            raise AuthorityError("partition_invalid", "candidate confidentiality is invalid")
        project = record.get("project")
        context_id = record.get("context_id")
        if project is not None and not _nonblank(project):
            raise AuthorityError("partition_invalid", "candidate project is invalid")
        if context_id is not None and not _nonblank(context_id):
            raise AuthorityError("partition_invalid", "candidate context is invalid")
        if record.get("scope") == "project" and project is None:
            raise AuthorityError("partition_invalid", "project-scoped candidate lacks project")

        if verify_provenance:
            try:
                source_resolution = verify_source_bindings(
                    self.root, self.state_dir, record
                )
            except SourceRefError as exc:
                if exc.code == "missing_source":
                    code = "provenance_missing"
                    message = "candidate provenance is missing"
                elif exc.code == "stale_source":
                    code = "provenance_changed"
                    message = "candidate source hash changed"
                else:
                    code = "provenance_invalid"
                    message = "candidate provenance is invalid"
                raise AuthorityError(code, message) from exc
        else:
            source_resolution = {
                "source_provenance": record.get("source_provenance"),
                "source_bindings": record.get("source_bindings"),
            }
            if (
                source_resolution["source_provenance"]
                not in {"verified", "manual_declaration"}
                or not isinstance(source_resolution["source_bindings"], list)
                or not source_resolution["source_bindings"]
            ):
                raise AuthorityError(
                    "provenance_invalid", "candidate provenance is invalid"
                )

        requested_action = record.get("requested_action", record.get("candidate_action"))
        candidate_action = record.get("candidate_action")
        target_intent = {
            "memory_key": record["id"],
            "requested_action": requested_action,
            "candidate_action": candidate_action,
            "target_id": record.get("target_id"),
            "memory_kind": record.get("type"),
            "scope": record.get("scope"),
            "workspace": workspace,
            "project": project,
            "context_id": context_id,
            "confidentiality": confidentiality,
        }
        partition = {
            "workspace": workspace,
            "project": project,
            "context_id": context_id,
            "confidentiality": confidentiality,
        }
        snapshot = {
            "candidate_id": record["id"],
            "relative_path": row["relative_path"],
            "status": status_value,
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "record_sha256": _digest(record),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "source_provenance": source_resolution["source_provenance"],
            "provenance_refs_sha256": _digest(
                source_resolution["source_bindings"]
            ),
            "target_intent_sha256": _digest(target_intent),
            "partition": partition,
            "requested_action": requested_action,
            "candidate_action": candidate_action,
        }
        return snapshot, record

    def snapshot_candidate(self, candidate_id: str):
        snapshot, _ = self._record_snapshot(candidate_id, require_candidate=True)
        return snapshot

    def current_generation(self) -> int:
        raw = _safe_regular_bytes(self.generation_path)
        value = _decode_canonical(raw, name="generation")
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "active_generation"}
            or value.get("schema_version") != 1
            or isinstance(value.get("active_generation"), bool)
            or not isinstance(value.get("active_generation"), int)
            or value["active_generation"] < 0
        ):
            raise AuthorityError("generation_invalid", "active generation is invalid")
        return value["active_generation"]

    def _set_generation(self, expected: int, target: int) -> None:
        current = self.current_generation()
        if current == target:
            return
        if current != expected:
            raise AuthorityError("generation_conflict", "active generation changed")
        _replace_canonical(
            self.generation_path,
            {"schema_version": 1, "active_generation": target},
        )

    @staticmethod
    def _decision_semantics(
        action,
        snapshot,
        reason,
        expected_generation,
        reviewer_scope,
    ):
        return {
            "schema_version": 1,
            "action": action,
            "candidate_snapshot": snapshot,
            "reason": reason,
            "expected_active_generation": expected_generation,
            "reviewer_scope": reviewer_scope,
        }

    @staticmethod
    def _validate_action(action, record):
        if action not in DECISION_ACTIONS:
            raise AuthorityError("invalid_action", "unsupported review action")
        requested = record.get("requested_action")
        candidate_action = record.get("candidate_action")
        if action == "reject":
            return
        if action == "conflict":
            if requested != "conflict" and candidate_action != "REVIEW_REQUIRED":
                raise AuthorityError("action_mismatch", "candidate does not request conflict")
            return
        if action == "merge":
            if requested not in {"merge", "support"}:
                raise AuthorityError("action_mismatch", "candidate does not request merge")
            return
        if requested in {"merge", "support", "conflict"} or candidate_action == "REVIEW_REQUIRED":
            raise AuthorityError("action_mismatch", "candidate requires an explicit action")

    def publish_decision(
        self,
        action: str,
        candidate_id: str,
        *,
        reason: str | None = None,
        workspace: str | None = None,
        confidentiality: str | None = None,
        validator=None,
    ):
        if workspace is None or workspace not in {"personal", "work"}:
            raise AuthorityError("invalid_workspace", "reviewer workspace is invalid")
        if confidentiality is None or confidentiality not in _REVIEWER_CONFIDENTIALITY:
            raise AuthorityError(
                "invalid_confidentiality",
                "reviewer confidentiality authorization is invalid",
            )
        reviewer_profile = ReviewerProfile(workspace, confidentiality)
        snapshot, record = self._record_snapshot(
            candidate_id,
            require_candidate=True,
            reviewer_profile=reviewer_profile,
        )
        self._validate_action(action, record)
        if validator is not None:
            if not callable(validator):
                raise AuthorityError("validator_invalid", "decision validator is invalid")
            validator(copy.deepcopy(record), copy.deepcopy(snapshot))
        if reason is None:
            reason = "rejected by reviewer" if action == "reject" else "approved by reviewer"
        if not _nonblank(reason):
            raise AuthorityError("invalid_reason", "reason must be non-empty")
        expected_generation = self.current_generation()
        semantics = self._decision_semantics(
            action,
            snapshot,
            reason,
            expected_generation,
            reviewer_profile.semantics(),
        )
        decision_id = "mdec_" + _digest(semantics)
        path = self.decisions / f"{decision_id}.json"
        if path.exists():
            decision = self.read_decision(decision_id)
            projection = {
                key: decision[key]
                for key in (
                    "schema_version",
                    "action",
                    "candidate_snapshot",
                    "reason",
                    "expected_active_generation",
                    "reviewer_scope",
                )
            }
            if projection != semantics:
                raise AuthorityError("decision_conflict", "decision conflicts")
            return decision
        decision = {
            **semantics,
            "decision_id": decision_id,
            "recorded_at": _now(),
        }
        decision["decision_sha256"] = _digest(decision)
        _write_once(path, decision)
        return copy.deepcopy(decision)

    def read_decision(self, decision_id: str):
        if not isinstance(decision_id, str) or _DECISION_ID.fullmatch(decision_id) is None:
            raise AuthorityError("invalid_decision_id", "decision_id is invalid")
        path = self.decisions / f"{decision_id}.json"
        try:
            raw = _safe_regular_bytes(path)
        except AuthorityError as exc:
            if exc.code == "path_read_failed":
                raise AuthorityError("decision_not_found", "decision not found") from exc
            raise
        decision = _decode_canonical(raw, name="decision")
        required = {
            "schema_version",
            "decision_id",
            "decision_sha256",
            "action",
            "candidate_snapshot",
            "reason",
            "expected_active_generation",
            "reviewer_scope",
            "recorded_at",
        }
        if not isinstance(decision, dict) or set(decision) != required:
            raise AuthorityError("decision_invalid", "decision is invalid")
        if decision.get("schema_version") != 1 or decision.get("decision_id") != decision_id:
            raise AuthorityError("decision_invalid", "decision is invalid")
        digest = decision.get("decision_sha256")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise AuthorityError("decision_invalid", "decision is invalid")
        body = dict(decision)
        body.pop("decision_sha256")
        if digest != _digest(body):
            raise AuthorityError("decision_invalid", "decision digest mismatch")
        reviewer_scope = decision.get("reviewer_scope")
        if not isinstance(reviewer_scope, dict) or set(reviewer_scope) != {
            "workspace",
            "confidentiality",
        }:
            raise AuthorityError("decision_invalid", "decision is invalid")
        try:
            reviewer_profile = ReviewerProfile(
                reviewer_scope.get("workspace"),
                reviewer_scope.get("confidentiality"),
            )
        except AuthorityError as exc:
            raise AuthorityError("decision_invalid", "decision is invalid") from exc
        semantics = self._decision_semantics(
            decision.get("action"),
            decision.get("candidate_snapshot"),
            decision.get("reason"),
            decision.get("expected_active_generation"),
            reviewer_profile.semantics(),
        )
        if decision_id != "mdec_" + _digest(semantics):
            raise AuthorityError("decision_invalid", "decision identity mismatch")
        return decision

    def _active_map(self):
        result = {}
        for row in self._rows():
            record = row["record"]
            status_value = record.get("status")
            if status_value == "active":
                raw = _safe_regular_bytes(row["path"], root=self.root)
                result[record["id"]] = {
                    "relative_path": row["relative_path"],
                    "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                }
        return result

    def _ensure_baseline(self):
        if self.baseline_path.exists():
            self._baseline()
            return
        existing = any(self.decisions.iterdir()) or any(self.activations.iterdir()) or any(
            self.pending.iterdir()
        )
        if existing or self.current_generation() != 0:
            raise AuthorityError("baseline_missing", "authority baseline is missing")
        payload = {
            "schema_version": 1,
            "created_at": _now(),
            "records": self._active_map(),
        }
        payload["baseline_sha256"] = _digest(payload)
        _write_once(self.baseline_path, payload)

    def _baseline(self):
        raw = _safe_regular_bytes(self.baseline_path)
        payload = _decode_canonical(raw, name="baseline")
        required = {"schema_version", "created_at", "records", "baseline_sha256"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise AuthorityError("baseline_invalid", "authority baseline is invalid")
        body = dict(payload)
        digest = body.pop("baseline_sha256", None)
        if payload.get("schema_version") != 1 or digest != _digest(body):
            raise AuthorityError("baseline_invalid", "authority baseline is invalid")
        records = payload.get("records")
        if not isinstance(records, dict):
            raise AuthorityError("baseline_invalid", "authority baseline is invalid")
        return payload

    def _receipt_path(self, decision_id: str) -> Path:
        return self.activations / f"{decision_id}.json"

    def _pending_path(self, decision_id: str) -> Path:
        return self.pending / f"{decision_id}.json"

    def _read_receipt(self, decision_id: str):
        path = self._receipt_path(decision_id)
        if not path.exists():
            return None
        receipt = _decode_canonical(_safe_regular_bytes(path), name="activation receipt")
        required = {
            "schema_version",
            "activation_id",
            "activation_sha256",
            "decision_id",
            "decision_sha256",
            "candidate_id",
            "candidate_before",
            "candidate_after",
            "action",
            "previous_generation",
            "active_generation",
            "authorized_records",
            "backend_result_sha256",
            "status",
            "committed_at",
        }
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise AuthorityError("activation_invalid", "activation receipt is invalid")
        if receipt.get("schema_version") != 1 or receipt.get("decision_id") != decision_id:
            raise AuthorityError("activation_invalid", "activation receipt is invalid")
        activation_id = receipt.get("activation_id")
        if not isinstance(activation_id, str) or _ACTIVATION_ID.fullmatch(activation_id) is None:
            raise AuthorityError("activation_invalid", "activation receipt is invalid")
        body = dict(receipt)
        digest = body.pop("activation_sha256", None)
        if digest != _digest(body):
            raise AuthorityError("activation_invalid", "activation receipt digest mismatch")
        identity = {
            key: receipt[key]
            for key in (
                "decision_id",
                "decision_sha256",
                "candidate_id",
                "candidate_before",
                "candidate_after",
                "action",
                "previous_generation",
                "active_generation",
                "authorized_records",
                "backend_result_sha256",
                "status",
            )
        }
        if activation_id != "mact_" + _digest(identity):
            raise AuthorityError("activation_invalid", "activation identity mismatch")
        return receipt

    def _remove_pending(self, path: Path) -> None:
        try:
            path.unlink()
            _fsync_directory(path.parent)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AuthorityError("pending_cleanup_failed", "pending activation cleanup failed") from exc

    @staticmethod
    def _postcondition(decision, record) -> bool:
        action = decision["action"]
        status_value = record.get("status")
        if status_value == "conflict":
            status_value = "conflicted"
        audit = record.get("audit_status")
        if action == "reject":
            return status_value == "archived" and audit == "rejected"
        if action == "conflict":
            return status_value == "conflicted" and audit == "conflict"
        return status_value in {"active", "archived"} and audit == "accepted"

    def _commit_receipt(
        self,
        *,
        decision,
        before,
        active_before,
        backend_result,
    ):
        candidate_after, record_after = self._record_snapshot(
            before["candidate_id"],
            require_candidate=False,
            verify_provenance=False,
        )
        if not self._postcondition(decision, record_after):
            raise AuthorityError("outcome_uncertain", "activation outcome is uncertain")
        active_after = self._active_map()
        authorized_records = {
            memory_id: value
            for memory_id, value in active_after.items()
            if active_before.get(memory_id) != value
        }
        previous = decision["expected_active_generation"]
        target = previous + 1
        result_hash = _digest(backend_result)
        identity = {
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
            "candidate_id": before["candidate_id"],
            "candidate_before": before,
            "candidate_after": candidate_after,
            "action": decision["action"],
            "previous_generation": previous,
            "active_generation": target,
            "authorized_records": authorized_records,
            "backend_result_sha256": result_hash,
            "status": "committed",
        }
        receipt = {
            "schema_version": 1,
            "activation_id": "mact_" + _digest(identity),
            **identity,
            "committed_at": _now(),
        }
        receipt["activation_sha256"] = _digest(receipt)
        _write_once(self._receipt_path(decision["decision_id"]), receipt)
        self._set_generation(previous, target)
        self._remove_pending(self._pending_path(decision["decision_id"]))
        return receipt

    def activate(self, decision_id: str, expected_active_generation: int, apply):
        if isinstance(expected_active_generation, bool) or not isinstance(
            expected_active_generation, int
        ) or expected_active_generation < 0:
            raise AuthorityError("generation_invalid", "expected generation is invalid")
        if not callable(apply):
            raise AuthorityError("activation_backend_invalid", "activation backend is invalid")

        with self.locked():
            decision = self.read_decision(decision_id)
            if decision["expected_active_generation"] != expected_active_generation:
                raise AuthorityError("generation_conflict", "decision generation mismatch")

            existing = self._read_receipt(decision_id)
            if existing is not None:
                self._set_generation(
                    existing["previous_generation"], existing["active_generation"]
                )
                self._remove_pending(self._pending_path(decision_id))
                return copy.deepcopy(existing), {"status": "idempotent"}

            current_generation = self.current_generation()
            if current_generation != expected_active_generation:
                raise AuthorityError("generation_conflict", "active generation changed")

            before = decision["candidate_snapshot"]
            pending_path = self._pending_path(decision_id)
            pending = None
            if pending_path.exists():
                pending = _decode_canonical(
                    _safe_regular_bytes(pending_path), name="pending activation"
                )
                if (
                    not isinstance(pending, dict)
                    or pending.get("schema_version") != 1
                    or pending.get("decision_id") != decision_id
                    or pending.get("decision_sha256") != decision["decision_sha256"]
                    or pending.get("candidate_before") != before
                    or pending.get("previous_generation") != expected_active_generation
                    or not isinstance(pending.get("active_before"), dict)
                ):
                    raise AuthorityError("pending_invalid", "pending activation is invalid")
            else:
                current, _ = self._record_snapshot(
                    before["candidate_id"], require_candidate=True
                )
                if current != before:
                    raise AuthorityError("stale_candidate", "candidate changed after decision")
                pending = {
                    "schema_version": 1,
                    "decision_id": decision_id,
                    "decision_sha256": decision["decision_sha256"],
                    "candidate_before": before,
                    "previous_generation": expected_active_generation,
                    "active_before": self._active_map(),
                    "prepared_at": _now(),
                }
                pending["pending_sha256"] = _digest(pending)
                _write_once(pending_path, pending)

            try:
                current, current_record = self._record_snapshot(
                    before["candidate_id"],
                    require_candidate=False,
                    verify_provenance=False,
                )
                if current == before:
                    verified_current, _ = self._record_snapshot(
                        before["candidate_id"], require_candidate=True
                    )
                    if verified_current != before:
                        raise AuthorityError(
                            "stale_candidate", "candidate changed after decision"
                        )
                    backend_result = apply(copy.deepcopy(decision))
                elif self._postcondition(decision, current_record):
                    backend_result = {"status": "recovered"}
                else:
                    raise AuthorityError(
                        "outcome_uncertain", "activation outcome is uncertain"
                    )
            except Exception:
                try:
                    current, _ = self._record_snapshot(
                        before["candidate_id"],
                        require_candidate=False,
                        verify_provenance=False,
                    )
                except Exception:
                    current = None
                if current == before:
                    self._remove_pending(pending_path)
                raise

            receipt = self._commit_receipt(
                decision=decision,
                before=before,
                active_before=pending["active_before"],
                backend_result=backend_result,
            )
            return copy.deepcopy(receipt), backend_result

    def pending_count(self) -> int:
        count = 0
        for path in self.pending.iterdir():
            if path.name.startswith(".tmp."):
                continue
            if path.suffix != ".json" or not path.is_file() or path.is_symlink():
                raise AuthorityError("pending_invalid", "pending namespace is invalid")
            count += 1
        return count

    def authorize_active_records(self, records):
        if self.pending_count():
            raise AuthorityError(
                "activation_pending", "active visibility is blocked by pending activation"
            )
        baseline = self._baseline()["records"]
        receipts = {}
        for path in self.activations.iterdir():
            if path.name.startswith(".tmp."):
                continue
            if path.suffix != ".json" or path.is_symlink() or not path.is_file():
                raise AuthorityError("activation_invalid", "activation namespace is invalid")
            decision_id = path.stem
            receipt = self._read_receipt(decision_id)
            for memory_id, binding in receipt["authorized_records"].items():
                existing = receipts.get(memory_id)
                if existing is not None and existing["binding"] != binding:
                    raise AuthorityError("activation_conflict", "active authorization conflicts")
                receipts[memory_id] = {
                    "binding": binding,
                    "activation_id": receipt["activation_id"],
                }

        authorized = []
        for source_record in records:
            record = copy.deepcopy(source_record)
            memory_id = record.get("id")
            relative_path = record.get("relative_path")
            if not _nonblank(memory_id) or not _nonblank(relative_path):
                raise AuthorityError("visibility_invalid", "active record binding is invalid")
            raw = _safe_regular_bytes(self.root / relative_path, root=self.root)
            binding = {
                "relative_path": relative_path,
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            }
            if baseline.get(memory_id) == binding:
                authorized.append(record)
                continue
            receipt = receipts.get(memory_id)
            if receipt is None or receipt["binding"] != binding:
                raise AuthorityError(
                    "activation_receipt_missing",
                    "active memory lacks a matching activation receipt",
                )
            record["authority_receipt_id"] = receipt["activation_id"]
            authorized.append(record)
        return authorized


class AuthorityMemoryStore:
    """Read facade that refuses post-baseline active records without receipts."""

    def __init__(self, store, authority: AuthorityStore):
        for name in ("records", "get", "reviewable", "active_relevant"):
            if not callable(getattr(store, name, None)):
                raise ValueError("memory store is invalid")
        if not isinstance(authority, AuthorityStore):
            raise ValueError("authority store is invalid")
        self.store = store
        self.authority = authority
        self.root = store.root

    def records(self):
        return self.store.records()

    def get(self, memory_id):
        return self.store.get(memory_id)

    def reviewable(self, workspace=None, project=None, statuses=None):
        return self.store.reviewable(workspace, project, statuses)

    def active_relevant(self, query, workspace=None, project=None, confidentiality=None):
        rows = self.store.active_relevant(query, workspace, project, confidentiality)
        return self.authority.authorize_active_records(rows)


__all__ = [
    "AuthorityError",
    "AuthorityMemoryStore",
    "AuthorityStore",
    "DECISION_ACTIONS",
    "ReviewerProfile",
    "canonical_bytes",
]
