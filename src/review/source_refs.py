"""Closed, fail-closed source-reference resolution for memory review.

Every accepted source reference uses an allowlisted scheme with an explicit
parser.  Resolved bindings are canonical JSON strings because the memory
front-matter format supports flat string lists; decision publication and
activation re-resolve those bindings byte-for-byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from pathlib import Path


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_POLICY_ID = re.compile(r"[0-9a-f]{16}\Z")
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
_WORKSPACES = frozenset({"personal", "work"})
_CONFIDENTIALITY = frozenset({"public", "personal", "internal", "restricted"})
_MANUAL_SOURCES = frozenset({"manual:user_confirmed", "user"})
_LEARNING_ARTIFACTS = frozenset(
    {
        "run.json",
        "context.json",
        "result.md",
        "evidence.json",
        "outcome.json",
        "reflection.md",
        "policy_suggestions.md",
        "review_decisions.json",
        "memory_rules.md",
        "comparison.json",
        "comparison.md",
    }
)
_INFORMATIONAL_LEARNING_KINDS = frozenset({"principle", "procedure"})
_MAX_SOURCE_BYTES = 64 * 1024 * 1024


class SourceRefError(ValueError):
    """Stable fail-closed source-resolution error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_bytes(value) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SourceRefError("invalid_source", "source artifact is not canonical") from exc


def _digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _nonblank(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _state_root(state_dir) -> Path:
    if state_dir is None:
        raise SourceRefError("missing_source", "source state directory is required")
    try:
        state = Path(state_dir).expanduser().resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise SourceRefError("missing_source", "source state directory is unavailable") from exc
    try:
        info = os.lstat(state)
    except OSError as exc:
        raise SourceRefError("missing_source", "source state directory is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SourceRefError("invalid_source", "source state directory is unsafe")
    return state


def _ensure_state_root(state_dir) -> Path:
    if state_dir is None:
        raise SourceRefError("missing_source", "source state directory is required")
    candidate = Path(state_dir).expanduser()
    try:
        if candidate.exists():
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SourceRefError("invalid_source", "source state directory is unsafe")
        else:
            candidate.mkdir(parents=True, mode=0o700)
        return _state_root(candidate)
    except SourceRefError:
        raise
    except OSError as exc:
        raise SourceRefError(
            "missing_source", "source state directory is unavailable"
        ) from exc


def _regular_bytes(path: Path, *, root: Path) -> bytes:
    """Read one bounded regular file through one no-follow descriptor."""

    try:
        root = Path(root).resolve(strict=True)
        path = Path(path)
        relative = path.relative_to(root)
    except (OSError, ValueError, TypeError) as exc:
        raise SourceRefError("invalid_source", "source path escapes its root") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SourceRefError("missing_source", "source artifact was not found") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SourceRefError("invalid_source", "source path contains a symlink")

    try:
        before = os.lstat(path)
    except OSError as exc:
        raise SourceRefError("missing_source", "source artifact was not found") from exc
    if not stat.S_ISREG(before.st_mode):
        raise SourceRefError("invalid_source", "source artifact is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceRefError("missing_source", "source artifact could not be opened") from exc
    try:
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise SourceRefError("invalid_source", "source artifact is not a regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_SOURCE_BYTES:
                raise SourceRefError("invalid_source", "source artifact is too large")
        opened_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise SourceRefError("stale_source", "source artifact changed during read") from exc

    def identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        )

    if not (
        identity(before)
        == identity(opened_before)
        == identity(opened_after)
        == identity(after)
    ):
        raise SourceRefError("stale_source", "source artifact changed during read")
    return b"".join(chunks)


def _json_bytes(raw: bytes, message: str):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRefError("invalid_source", message) from exc
    if not isinstance(value, dict):
        raise SourceRefError("invalid_source", message)
    return value


def _partition(workspace, project, confidentiality) -> dict:
    if workspace not in _WORKSPACES:
        raise SourceRefError("invalid_source", "source workspace is invalid")
    if confidentiality not in _CONFIDENTIALITY:
        raise SourceRefError("invalid_source", "source confidentiality is invalid")
    if project is not None and not _nonblank(project):
        raise SourceRefError("invalid_source", "source project is invalid")
    return {
        "workspace": workspace,
        "project": project,
        "confidentiality": confidentiality,
    }


def _candidate_partition(record) -> dict:
    return _partition(
        record.get("workspace"),
        record.get("project"),
        record.get("confidentiality"),
    )


def _partition_matches(source: dict, candidate: dict, record: dict) -> bool:
    if source == candidate:
        return True
    return (
        record.get("requested_action") == "context_transition"
        and source.get("project") == candidate.get("project")
        and source.get("workspace") == "personal"
        and source.get("confidentiality") == "personal"
        and candidate.get("workspace") == "work"
        and candidate.get("confidentiality") in {"internal", "restricted"}
    )


def _verified(canonical_id: str, sha256: str, partition: dict, kind: str) -> dict:
    if not _nonblank(canonical_id) or _SHA256.fullmatch(sha256 or "") is None:
        raise SourceRefError("invalid_source", "resolved source binding is invalid")
    return {
        "assurance": "verified",
        "canonical_id": canonical_id,
        "sha256": sha256,
        "kind": kind,
        **partition,
    }


def _informational(ref: str, kind: str) -> dict:
    return {
        "assurance": "informational",
        "canonical_id": ref,
        "sha256": hashlib.sha256(ref.encode("utf-8")).hexdigest(),
        "kind": kind,
    }


def _manual_declaration(record) -> dict:
    declaration = {
        "source": record.get("source"),
        "confirmation": record.get("confirmation"),
    }
    return {
        "assurance": "manual_declaration",
        "canonical_id": f"manual-declaration:{record.get('source')}",
        "sha256": _digest(declaration),
        "kind": "manual_declaration",
    }


def _file_partition(root: Path, normalized: str) -> dict:
    from memory import collect_document_items, collect_validated_records

    try:
        _, rows, errors, _ = collect_validated_records(root)
    except Exception as exc:
        raise SourceRefError("invalid_source", "memory source index is invalid") from exc
    if errors:
        raise SourceRefError("invalid_source", "memory source index is invalid")
    matches = [row for row in rows if row.get("relative_path") == normalized]
    if len(matches) == 1:
        record = matches[0]["record"]
        return _partition(
            record.get("workspace"),
            record.get("project"),
            record.get("confidentiality"),
        )
    if len(matches) > 1:
        raise SourceRefError("invalid_source", "file source identity is ambiguous")

    try:
        documents = collect_document_items(root)
    except Exception as exc:
        raise SourceRefError("invalid_source", "document source index is invalid") from exc
    document_matches = [
        item for item in documents if item.get("relative_path") == normalized
    ]
    if len(document_matches) == 1:
        item = document_matches[0]
        return _partition(
            item.get("workspace"), item.get("project"), item.get("confidentiality")
        )
    if len(document_matches) > 1:
        raise SourceRefError("invalid_source", "file source identity is ambiguous")
    return _partition("personal", None, "personal")


def _resolve_file(root: Path, value: str, expected_sha256: str | None = None) -> dict:
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise SourceRefError("invalid_source", "file source reference is invalid")
    normalized = relative.as_posix()
    raw = _regular_bytes(root / relative, root=root)
    actual = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        if (
            not isinstance(expected_sha256, str)
            or _SHA256.fullmatch(expected_sha256.lower()) is None
        ):
            raise SourceRefError("invalid_source", "source file digest is invalid")
        if actual != expected_sha256.lower():
            raise SourceRefError("stale_source", "source hash changed")
    return _verified(
        f"file:{normalized}", actual, _file_partition(root, normalized), "file"
    )


def _resolve_memory(root: Path, memory_id: str) -> dict:
    if not _nonblank(memory_id):
        raise SourceRefError("invalid_source", "memory source reference is invalid")
    from memory import collect_validated_records

    try:
        _, rows, errors, _ = collect_validated_records(root)
    except Exception as exc:
        raise SourceRefError("invalid_source", "memory source index is invalid") from exc
    if errors:
        raise SourceRefError("invalid_source", "memory source index is invalid")
    matches = [row for row in rows if row["record"].get("id") == memory_id]
    if len(matches) != 1:
        raise SourceRefError("missing_source", "memory source was not found")
    row = matches[0]
    raw = _regular_bytes(row["path"], root=root)
    record = row["record"]
    partition = _partition(
        record.get("workspace"),
        record.get("project"),
        record.get("confidentiality"),
    )
    return _verified(
        f"memory:{memory_id}", hashlib.sha256(raw).hexdigest(), partition, "memory"
    )


def _resolve_document(root: Path, document_id: str) -> dict:
    if not _nonblank(document_id):
        raise SourceRefError("invalid_source", "document source reference is invalid")
    from memory import collect_document_items

    try:
        matches = [
            item for item in collect_document_items(root) if item.get("id") == document_id
        ]
    except Exception as exc:
        raise SourceRefError("invalid_source", "document source index is invalid") from exc
    if len(matches) != 1:
        raise SourceRefError("missing_source", "document source was not found")
    item = matches[0]
    partition = _partition(
        item.get("workspace"), item.get("project"), item.get("confidentiality")
    )
    digest = item.get("sha256") or item.get("content_sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise SourceRefError("invalid_source", "document source digest is invalid")
    return _verified(f"doc:{document_id}", digest, partition, "document")


def _resolve_session(state_dir, session_id: str) -> dict:
    if not _nonblank(session_id):
        raise SourceRefError("invalid_source", "session source reference is invalid")
    state = _state_root(state_dir)
    database = state / "sessions.sqlite"
    _regular_bytes(database, root=state)
    try:
        connection = sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            session = connection.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise SourceRefError("missing_source", "session source was not found")
            messages = connection.execute(
                "SELECT ordinal,role,content,created_at,metadata_json "
                "FROM messages WHERE session_id=? ORDER BY ordinal",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
    except SourceRefError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SourceRefError("invalid_source", "session source store is invalid") from exc
    try:
        metadata = json.loads(session["metadata_json"])
        payload = {
            "session": {
                key: session[key] for key in session.keys() if key != "metadata_json"
            },
            "metadata": metadata,
            "messages": [
                {
                    "ordinal": row["ordinal"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "metadata": json.loads(row["metadata_json"]),
                }
                for row in messages
            ],
        }
    except (TypeError, json.JSONDecodeError) as exc:
        raise SourceRefError("invalid_source", "session source metadata is invalid") from exc
    workspace = session["workspace"]
    confidentiality = metadata.get("confidentiality") or (
        "personal" if workspace == "personal" else "internal"
    )
    return _verified(
        f"session:{session_id}",
        _digest(payload),
        _partition(workspace, session["project"], confidentiality),
        "session",
    )


def _resolve_loop_run(state_dir, run_id: str) -> dict:
    if _RUN_ID.fullmatch(run_id or "") is None:
        raise SourceRefError("invalid_source", "loop-run source reference is invalid")
    state = _state_root(state_dir)
    run_dir = state / "loop_engineering" / "runs" / run_id
    try:
        info = os.lstat(run_dir)
    except OSError as exc:
        raise SourceRefError("missing_source", "loop-run source was not found") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SourceRefError("invalid_source", "loop-run source path is invalid")
    raw = _regular_bytes(run_dir / "run.json", root=state)
    run = _json_bytes(raw, "loop-run source is invalid")
    if run.get("run_id") != run_id:
        raise SourceRefError("invalid_source", "loop-run source is invalid")
    reflection_path = run_dir / "reflection.md"
    reflection_sha = None
    try:
        reflection_sha = hashlib.sha256(
            _regular_bytes(reflection_path, root=state)
        ).hexdigest()
    except SourceRefError as exc:
        if exc.code != "missing_source":
            raise
    stable = {
        "schema_version": run.get("schema_version"),
        "contract_version": run.get("contract_version"),
        "contract_id": run.get("contract_id"),
        "run_id": run_id,
        "task_sha256": run.get("task_sha256"),
        "result_sha256": run.get("result_sha256"),
        "outcome": run.get("outcome"),
        "workspace": run.get("workspace", "personal"),
        "project": run.get("project"),
        "reflection_sha256": reflection_sha,
    }
    if run.get("schema_version") == 2 and (
        _SHA256.fullmatch(stable["task_sha256"] or "") is None
        or _SHA256.fullmatch(stable["result_sha256"] or "") is None
    ):
        raise SourceRefError("invalid_source", "loop-run source digest is invalid")
    workspace = stable["workspace"]
    confidentiality = "personal" if workspace == "personal" else "internal"
    return _verified(
        f"loop-run:{run_id}",
        _digest(stable) if run.get("schema_version") == 2 else hashlib.sha256(raw).hexdigest(),
        _partition(workspace, stable["project"], confidentiality),
        "loop_run",
    )


def _learning_run_dir(state: Path, run_id: str) -> Path:
    if not _nonblank(run_id) or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise SourceRefError("invalid_source", "learning-run source reference is invalid")
    return state / "learning_runs" / run_id


def _resolve_learning_run(state_dir, run_id: str, name: str) -> dict:
    ref = f"learning-run:{run_id}:{name}"
    if name in _INFORMATIONAL_LEARNING_KINDS:
        return _informational(ref, "learning_run_annotation")
    if name not in _LEARNING_ARTIFACTS:
        raise SourceRefError("invalid_source", "learning-run artifact is not allowed")
    state = _state_root(state_dir)
    run_dir = _learning_run_dir(state, run_id)
    try:
        info = os.lstat(run_dir)
    except OSError as exc:
        raise SourceRefError("missing_source", "learning-run source was not found") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SourceRefError("invalid_source", "learning-run source path is invalid")
    run = _json_bytes(
        _regular_bytes(run_dir / "run.json", root=state),
        "learning-run source is invalid",
    )
    target_raw = _regular_bytes(run_dir / name, root=state)
    task = run.get("task") if isinstance(run, dict) else None
    if not isinstance(task, dict) or run.get("run_id") != run_id:
        raise SourceRefError("invalid_source", "learning-run source is invalid")
    workspace = task.get("workspace")
    confidentiality = task.get("candidate_confidentiality") or task.get(
        "confidentiality"
    ) or ("personal" if workspace == "personal" else "internal")
    return _verified(
        ref,
        hashlib.sha256(target_raw).hexdigest(),
        _partition(workspace, task.get("project"), confidentiality),
        "learning_run_artifact",
    )


def _resolve_candidate_request(state_dir, request_id: str) -> dict:
    if _REQUEST_ID.fullmatch(request_id or "") is None:
        raise SourceRefError("invalid_source", "loop candidate request is invalid")
    state = _state_root(state_dir)
    path = (
        state
        / "loop_engineering"
        / "generated_candidates"
        / f"{request_id}.json"
    )
    intent = _json_bytes(
        _regular_bytes(path, root=state),
        "loop candidate request artifact is invalid",
    )
    if intent.get("request_id") != request_id:
        raise SourceRefError("invalid_source", "loop candidate request artifact is invalid")
    immutable = {
        key: intent.get(key)
        for key in (
            "schema_version",
            "contract_id",
            "request_id",
            "policy_id",
            "policy_text",
            "workspace",
            "project",
            "minimum_independent_evidence",
            "evidence_run_ids",
            "evidence_fingerprints",
            "applied",
        )
    }
    workspace = intent.get("workspace")
    confidentiality = "personal" if workspace == "personal" else "internal"
    return _verified(
        f"loop-candidate-request:{request_id}",
        _digest(immutable),
        _partition(workspace, intent.get("project"), confidentiality),
        "loop_candidate_request",
    )


def _resolve_state_artifact(state_dir, digest: str) -> dict:
    if _SHA256.fullmatch(digest or "") is None:
        raise SourceRefError("invalid_source", "artifact source reference is invalid")
    state = _state_root(state_dir)
    raw = _regular_bytes(state / "source_artifacts" / f"{digest}.json", root=state)
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise SourceRefError("stale_source", "source artifact hash changed")
    artifact = _json_bytes(raw, "source artifact is invalid")
    required = {
        "schema_version",
        "kind",
        "workspace",
        "project",
        "confidentiality",
        "payload",
        "payload_sha256",
    }
    if (
        set(artifact) != required
        or artifact.get("schema_version") != 1
        or not _nonblank(artifact.get("kind"))
        or artifact.get("payload_sha256") != _digest(artifact.get("payload"))
        or canonical_bytes(artifact) != raw
    ):
        raise SourceRefError("invalid_source", "source artifact is invalid")
    partition = _partition(
        artifact.get("workspace"),
        artifact.get("project"),
        artifact.get("confidentiality"),
    )
    return _verified(f"artifact:{digest}", actual, partition, artifact["kind"])


def publish_source_artifact(
    state_dir,
    *,
    kind: str,
    workspace: str,
    project,
    confidentiality: str,
    payload,
) -> str:
    """Write one immutable canonical source artifact and return its reference."""

    if not _nonblank(kind):
        raise SourceRefError("invalid_source", "source artifact kind is invalid")
    partition = _partition(workspace, project, confidentiality)
    body = {
        "schema_version": 1,
        "kind": kind.strip(),
        **partition,
        "payload": payload,
        "payload_sha256": _digest(payload),
    }
    raw = canonical_bytes(body)
    digest = hashlib.sha256(raw).hexdigest()
    state = _ensure_state_root(state_dir)
    directory = state / "source_artifacts"
    try:
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise SourceRefError("invalid_source", "source artifact directory is unsafe")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.json"
        if target.exists():
            if _regular_bytes(target, root=state) != raw:
                raise SourceRefError("stale_source", "source artifact identity conflicts")
            return f"artifact:{digest}"
        temporary = directory / f".tmp.{digest}.{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _regular_bytes(target, root=state) != raw:
                raise SourceRefError("stale_source", "source artifact identity conflicts")
        finally:
            temporary.unlink(missing_ok=True)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SourceRefError:
        raise
    except OSError as exc:
        raise SourceRefError("invalid_source", "source artifact could not be written") from exc
    return f"artifact:{digest}"


def _parse_ref(root: Path, state_dir, ref: str) -> dict:
    if not _nonblank(ref) or ref != ref.strip() or ":" not in ref:
        raise SourceRefError("invalid_source", "source reference is malformed")
    scheme, value = ref.split(":", 1)
    if scheme == "file":
        return _resolve_file(root, value)
    if scheme == "memory":
        return _resolve_memory(root, value)
    if scheme == "doc":
        return _resolve_document(root, value)
    if scheme == "session":
        return _resolve_session(state_dir, value)
    if scheme == "artifact":
        return _resolve_state_artifact(state_dir, value)
    if scheme == "loop-run":
        return _resolve_loop_run(state_dir, value)
    if scheme == "learning-run":
        parts = value.split(":", 1)
        if len(parts) != 2:
            raise SourceRefError("invalid_source", "learning-run source reference is invalid")
        return _resolve_learning_run(state_dir, parts[0], parts[1])
    if scheme == "loop-candidate-request":
        return _resolve_candidate_request(state_dir, value)
    if scheme == "loop-policy":
        if _POLICY_ID.fullmatch(value or "") is None:
            raise SourceRefError("invalid_source", "loop-policy reference is invalid")
        return _informational(ref, "loop_policy")
    if scheme in {"run", "criterion"}:
        if not _nonblank(value) or "/" in value or "\\" in value:
            raise SourceRefError("invalid_source", f"{scheme} reference is invalid")
        return _informational(ref, f"{scheme}_annotation")
    if scheme == "manual":
        if not _nonblank(value) or len(value.encode("utf-8")) > 4096 or "\x00" in value:
            raise SourceRefError("invalid_source", "manual reference is invalid")
        return _informational(ref, "manual_annotation")
    if scheme == "memory-agent":
        parts = value.split(":", 1)
        if (
            len(parts) != 2
            or parts[0] not in {"task", "result"}
            or _SHA256.fullmatch(parts[1]) is None
        ):
            raise SourceRefError("invalid_source", "memory-agent reference is invalid")
        return _informational(ref, f"memory_agent_{parts[0]}")
    raise SourceRefError("invalid_source", "source reference scheme is not allowed")


def resolve_source_bindings(root, state_dir, record) -> dict:
    """Resolve candidate provenance into canonical stored bindings."""

    try:
        root = Path(root).expanduser().resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise SourceRefError("invalid_source", "candidate source root is invalid") from exc
    if not isinstance(record, dict):
        raise SourceRefError("invalid_source", "candidate source record is invalid")
    candidate_partition = _candidate_partition(record)
    refs = []
    for field in ("source_refs", "evidence"):
        value = record.get(field, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise SourceRefError("invalid_source", "candidate source references are invalid")
        for item in value:
            if not _nonblank(item) or item in refs:
                raise SourceRefError("invalid_source", "candidate source references are invalid")
            refs.append(item)

    source_path = record.get("source_path")
    source_sha256 = record.get("source_sha256")
    if bool(source_path) != bool(source_sha256):
        raise SourceRefError(
            "missing_source", "source_path and source_sha256 must be provided together"
        )

    bindings = []
    for ref in refs:
        binding = _parse_ref(root, state_dir, ref)
        if binding.get("assurance") == "verified":
            source_partition = {
                "workspace": binding["workspace"],
                "project": binding["project"],
                "confidentiality": binding["confidentiality"],
            }
            if not _partition_matches(source_partition, candidate_partition, record):
                raise SourceRefError(
                    "partition_mismatch", "source partition does not match candidate"
                )
        bindings.append(binding)

    if source_path:
        binding = _resolve_file(root, source_path, source_sha256)
        source_partition = {
            "workspace": binding["workspace"],
            "project": binding["project"],
            "confidentiality": binding["confidentiality"],
        }
        if not _partition_matches(source_partition, candidate_partition, record):
            raise SourceRefError(
                "partition_mismatch", "source partition does not match candidate"
            )
        bindings.append(binding)

    if record.get("source") in _MANUAL_SOURCES:
        bindings.append(_manual_declaration(record))

    unique = {}
    for binding in bindings:
        key = (binding.get("assurance"), binding.get("canonical_id"))
        existing = unique.get(key)
        if existing is not None and existing != binding:
            raise SourceRefError("stale_source", "source identity resolves inconsistently")
        unique[key] = binding
    bindings = list(unique.values())
    verified = [item for item in bindings if item.get("assurance") == "verified"]
    manual = [
        item for item in bindings if item.get("assurance") == "manual_declaration"
    ]
    if not verified and not manual:
        raise SourceRefError("missing_source", "candidate requires a verifiable source")
    provenance = "verified" if verified else "manual_declaration"
    encoded = sorted(
        json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        for item in bindings
    )
    return {"source_provenance": provenance, "source_bindings": encoded}


def verify_source_bindings(root, state_dir, record) -> dict:
    stored_provenance = record.get("source_provenance")
    stored_bindings = record.get("source_bindings")
    if (
        stored_provenance not in {"verified", "manual_declaration"}
        or not isinstance(stored_bindings, list)
        or not stored_bindings
        or not all(_nonblank(item) for item in stored_bindings)
    ):
        raise SourceRefError("missing_source", "candidate source binding is missing")
    try:
        current = resolve_source_bindings(root, state_dir, record)
    except SourceRefError as exc:
        if exc.code in {"missing_source", "stale_source", "partition_mismatch"}:
            raise SourceRefError("stale_source", "source binding changed") from exc
        raise
    if (
        current["source_provenance"] != stored_provenance
        or current["source_bindings"] != stored_bindings
    ):
        raise SourceRefError("stale_source", "source binding changed")
    return current


__all__ = [
    "SourceRefError",
    "canonical_bytes",
    "publish_source_artifact",
    "resolve_source_bindings",
    "verify_source_bindings",
]
