import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from memory import (
    CONFIDENTIALITY_CHOICES,
    CONFIDENCE_CHOICES,
    SCOPE_CHOICES,
    TYPE_CHOICES,
    TYPE_DIRS,
    WORKSPACE_CHOICES,
    _replace_transaction,
    _require_data_root,
    _validate_hypothetical_records,
    atomic_write_text,
    collect_validated_records,
    default_state_dir,
    index_store,
    inspect_database,
    render_existing_memory,
    render_memory,
    safe_slug,
    validate_record,
)


CANONICAL_ACTIONS = {"ADD", "UPDATE", "DEPRECATE", "NOOP", "REVIEW_REQUIRED"}
ACTION_ALIASES = {
    "create": "ADD", "merge": "UPDATE", "support": "UPDATE",
    "supersede": "DEPRECATE", "conflict": "REVIEW_REQUIRED", "discard": "NOOP",
}
REVIEW_ACTIONS = CANONICAL_ACTIONS | set(ACTION_ALIASES)
SAFE_MERGE_LISTS = ("source_refs", "tags", "relations")
ERROR_CODES = {
    "missing_source", "invalid_schema", "invalid_transition", "duplicate_content",
    "conflict_requires_review", "permission_denied", "raw_integrity_violation",
    "apply_failed", "index_failed", "verify_failed", "repeated_failure",
    "max_iterations_reached", "stale_target",
}
STOP_STATUSES = {
    "completed", "no_change", "waiting_for_human", "missing_source", "low_confidence",
    "conflict_requires_review", "permission_denied", "repeated_failure",
    "max_iterations_reached", "apply_failed", "raw_integrity_violation",
    "index_failed", "verify_failed",
}


class DistillError(ValueError):
    def __init__(self, code, message, recoverable=False, next_action="review"):
        super().__init__(message)
        self.feedback = {
            "code": code, "recoverable": recoverable,
            "message": message, "next_action": next_action,
        }


def _today():
    return date.today().isoformat()


def _timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _journal_path(root):
    return root / "state/distill_runs.jsonl"


def _append_journal(root, **event):
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": event["run_id"],
        "status": event["status"],
        "iteration": event.get("iteration", 1),
        "max_iterations": event.get("max_iterations", 2),
        "input_hash": event.get("input_hash"),
        "proposal_ids": event.get("proposal_ids", []),
        "errors": event.get("errors", []),
        "stop_reason": event.get("stop_reason"),
        "next_action": event.get("next_action"),
        "resume_command": event.get("resume_command"),
        "timestamp": _timestamp(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _journal_entries(root):
    path = _journal_path(root)
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DistillError("invalid_schema", f"malformed journal line {number}", False, "repair_tail_manually") from exc
        if not isinstance(entry, dict) or not entry.get("run_id"):
            raise DistillError("invalid_schema", f"malformed journal line {number}", False, "repair_tail_manually")
        entries.append(entry)
    return entries


def resume_run(args):
    root = _require_data_root(args.root)
    rows = [entry for entry in _journal_entries(root) if entry["run_id"] == args.run_id]
    if not rows:
        raise DistillError("invalid_schema", "distill run not found", False, "check_run_id")
    return rows[-1]


def _records_by_id(root):
    root, records, errors, _ = collect_validated_records(root)
    if errors:
        raise ValueError("\n".join(f"ERROR {rel_path}: {message}" for rel_path, message in errors))
    return root, {item["record"]["id"]: item for item in records}


def _new_candidate_path(root, memory_type, title):
    target_dir = root / TYPE_DIRS[memory_type]
    day_id = _today().replace("-", "")
    slug = safe_slug(title)
    while True:
        candidate_id = f"{memory_type}-{day_id}-{uuid.uuid4().hex[:8]}"
        path = target_dir / f"{candidate_id}-{slug}.md"
        if not path.exists():
            return candidate_id, path


def _list(value):
    return value if isinstance(value, list) else []


def _merge_unique(left, right):
    result = list(left)
    for value in right:
        if value not in result:
            result.append(value)
    return result


def _input_hash(args):
    fields = {
        name: getattr(args, name, None)
        for name in (
            "action", "type", "title", "scope", "workspace", "confidentiality",
            "source", "confidence", "content", "target_id", "project", "context_id",
            "source_id", "source_path", "source_sha256", "evidence", "source_refs",
            "relations", "tags", "confirmed",
        )
    }
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _normalized_action(requested, records, args):
    action = ACTION_ALIASES.get(requested, requested)
    if action not in CANONICAL_ACTIONS:
        raise DistillError("invalid_schema", "invalid candidate action", False, "choose_valid_action")
    for item in records.values():
        record = item["record"]
        if record.get("content") == args.content:
            return "NOOP", record["id"]
        if args.source_sha256 and record.get("source_sha256") == args.source_sha256:
            return "NOOP", record["id"]
    if action == "ADD" and any(item["record"].get("title") == args.title for item in records.values()):
        return "REVIEW_REQUIRED", None
    return action, None


def _validate_proposal(root, records, args, action):
    source_refs = _list(args.source_refs) + _list(args.evidence)
    file_source = bool(args.source_path and args.source_sha256)
    if not source_refs and args.source != "manual:user_confirmed" and not file_source:
        raise DistillError("missing_source", "candidate requires source_refs or a hashed source file", True, "add_source")
    if bool(args.source_path) != bool(args.source_sha256):
        raise DistillError("missing_source", "source_path and source_sha256 must be provided together", True, "add_source_hash")
    if file_source:
        path = root / args.source_path
        if not path.is_file():
            raise DistillError("missing_source", "source file not found", True, "restore_source")
        if hashlib.sha256(path.read_bytes()).hexdigest() != args.source_sha256:
            raise DistillError("raw_integrity_violation", "source hash changed", False, "review_raw_source")
    if args.scope == "project" and not args.project:
        raise DistillError("invalid_schema", "project scope requires project", True, "add_project")
    active_projects = {
        item["record"].get("project") for item in records.values()
        if item["record"].get("type") == "project" and item["record"].get("status") == "active"
    }
    if args.scope == "project" and args.type != "project" and args.project not in active_projects:
        raise DistillError("invalid_schema", "project does not reference an active project", True, "select_project")
    if args.confidentiality in {"internal", "restricted"} and args.workspace != "work":
        raise DistillError("permission_denied", "confidentiality internal or restricted requires workspace work", False, "use_work_workspace")
    if action in {"UPDATE", "DEPRECATE"} and not args.target_id:
        raise DistillError("invalid_transition", "target-id is required for this action", True, "select_target")
    if args.target_id and args.target_id not in records:
        raise DistillError("invalid_transition", "target memory not found", True, "select_target")
    if action in {"UPDATE", "DEPRECATE"} and records[args.target_id]["record"].get("status") != "active":
        raise DistillError("invalid_transition", "only active memory may be replaced", False, "select_active_target")


def _verify_index(root, state_dir, records):
    db = Path(state_dir).expanduser() / "memory.sqlite"
    inspect_database(db)
    with closing(sqlite3.connect(db)) as conn:
        for record, relative_path, digest in records:
            row = conn.execute("SELECT status, sha256 FROM memories WHERE id=?", (record["id"],)).fetchone()
            if row != (record["status"], digest):
                raise DistillError("verify_failed", f"memory index hash mismatch: {record['id']}", True, "resume_verify")
            if conn.execute("SELECT COUNT(*) FROM memory_fts WHERE id=?", (record["id"],)).fetchone()[0] != 1:
                raise DistillError("verify_failed", f"memory FTS mismatch: {record['id']}", True, "resume_verify")
            visible = record["status"] == "active" and record.get("confidentiality") != "restricted" and not record.get("project")
            found = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE id=? AND status='active' AND COALESCE(confidentiality,'')!='restricted' AND project IS NULL",
                (record["id"],),
            ).fetchone()[0] == 1
            if found != visible:
                raise DistillError("verify_failed", f"search policy mismatch: {record['id']}", True, "resume_verify")


def _apply_files_and_index(root, state_dir, operations, expected_records):
    previous = {path: path.read_text(encoding="utf-8") for path, _ in operations if path.exists()}
    new_paths = [path for path, _ in operations if not path.exists()]
    try:
        _replace_transaction(operations, [])
    except Exception as exc:
        raise DistillError("apply_failed", str(exc), True, "resume_apply") from exc
    stage = "index_failed"
    try:
        index_store(argparse.Namespace(root=str(root), state_dir=str(state_dir), dry_run=False))
        stage = "verify_failed"
        verified = []
        for path, record in expected_records:
            verified.append((record, path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
        _verify_index(root, state_dir, verified)
    except Exception as exc:
        restore = [(path, content) for path, content in previous.items()]
        if restore:
            _replace_transaction(restore, [])
        for path in new_paths:
            path.unlink(missing_ok=True)
        try:
            index_store(argparse.Namespace(root=str(root), state_dir=str(state_dir), dry_run=False))
        except Exception:
            pass
        if isinstance(exc, DistillError):
            raise
        raise DistillError(stage, str(exc), True, "resume") from exc


def _apply_candidate_once(args):
    root = _require_data_root(args.root)
    _, records = _records_by_id(root)
    action, duplicate_id = _normalized_action(args.action, records, args)
    _validate_proposal(root, records, args, action)
    if action == "NOOP":
        return {
            "candidate_id": duplicate_id, "path": None, "action": action,
            "status": "no_change", "stop_reason": "no_change",
        }
    if args.dry_run:
        return {"candidate_id": None, "path": None, "action": action, "status": "waiting_for_human"}

    today = _today()
    candidate_id, path = _new_candidate_path(root, args.type, args.title)
    confidence = args.confidence
    if args.source.lower().startswith("codex") and confidence == "confirmed":
        confidence = "inferred"
    record = {
        "id": candidate_id,
        "type": args.type,
        "title": args.title,
        "created": today,
        "updated": today,
        "status": "candidate",
        "audit_status": "awaiting_review",
        "scope": args.scope,
        "workspace": args.workspace,
        "confidentiality": args.confidentiality,
        "source": args.source,
        "confidence": confidence,
        "candidate_action": action,
        "requested_action": args.action,
        "content": args.content,
        "tags": args.tags or [],
    }
    if args.confirmed:
        record["confirmation"] = "explicit"
    for field in ["target_id", "project", "context_id", "source_id", "source_path", "source_sha256"]:
        value = getattr(args, field)
        if value:
            record[field] = value
    if action in {"UPDATE", "DEPRECATE"}:
        target_item = records[args.target_id]
        record["expected_target_status"] = target_item["record"]["status"]
        record["expected_target_sha256"] = hashlib.sha256(target_item["path"].read_bytes()).hexdigest()
    for field in ["evidence", "source_refs", "relations"]:
        value = getattr(args, field)
        if value:
            record[field] = value

    validation_errors = validate_record(record, path, path.relative_to(root).as_posix(), args.type)
    if validation_errors:
        raise DistillError("invalid_schema", "; ".join(validation_errors), True, "fix_schema")

    rendered = render_memory(record)
    atomic_write_text(path, rendered)
    status = "low_confidence" if args.confidence == "uncertain" else "waiting_for_human"
    return {
        "candidate_id": candidate_id, "path": path.relative_to(root).as_posix(),
        "action": action, "status": status,
        "stop_reason": "conflict_requires_review" if action == "REVIEW_REQUIRED" else status,
    }


def apply_candidate(args):
    root = _require_data_root(args.root)
    run_id = uuid.uuid4().hex
    input_hash = _input_hash(args)
    max_iterations = args.max_iterations
    if max_iterations < 1 or max_iterations > 2:
        raise DistillError("invalid_schema", "max_iterations must be between 1 and 2", False, "set_max_iterations")
    previous_code = None
    for iteration in range(1, max_iterations + 1):
        try:
            result = _apply_candidate_once(args)
            result["run_id"] = run_id
            if args.dry_run:
                return result
            _append_journal(
                root, run_id=run_id, status=result["status"], iteration=iteration,
                max_iterations=max_iterations, input_hash=input_hash,
                proposal_ids=[result["candidate_id"]] if result.get("candidate_id") else [],
                stop_reason=result.get("stop_reason", result["status"]),
                next_action="accept" if result["status"] == "waiting_for_human" else ("review" if result["status"] == "low_confidence" else None),
                resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
            )
            return result
        except DistillError as exc:
            feedback = exc.feedback
            _append_journal(
                root, run_id=run_id, status=feedback["code"], iteration=iteration,
                max_iterations=max_iterations, input_hash=input_hash, errors=[feedback],
                stop_reason=feedback["code"] if not feedback["recoverable"] else None,
                next_action=feedback["next_action"],
                resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
            )
            if not feedback["recoverable"]:
                raise
            if previous_code == feedback["code"]:
                final = DistillError("repeated_failure", f"repeated_failure: {feedback['message']}", False, feedback["next_action"])
                _append_journal(
                    root, run_id=run_id, status="repeated_failure", iteration=iteration,
                    max_iterations=max_iterations, input_hash=input_hash, errors=[feedback],
                    stop_reason="repeated_failure", next_action=feedback["next_action"],
                    resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
                )
                raise final
            previous_code = feedback["code"]
    final = DistillError("max_iterations_reached", "max_iterations_reached", False, "review")
    _append_journal(
        root, run_id=run_id, status="max_iterations_reached", iteration=max_iterations,
        max_iterations=max_iterations, input_hash=input_hash, errors=[final.feedback],
        stop_reason="max_iterations_reached", next_action="review",
        resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
    )
    raise final


def _require_candidate(records, candidate_id):
    item = records.get(candidate_id)
    if item is None:
        raise ValueError("candidate not found")
    if item["record"].get("status") != "candidate":
        raise ValueError("memory is not awaiting candidate review")
    return item


def _check_source(root, record):
    source_path = record.get("source_path")
    source_sha256 = record.get("source_sha256")
    if not source_path or not source_sha256:
        return
    path = root / source_path
    if not path.is_file():
        raise DistillError("missing_source", "source file not found", True, "restore_source")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source_sha256:
        raise DistillError("raw_integrity_violation", "source hash changed", False, "review_raw_source")


def _reviewed(record, status, audit_status, reason=None):
    reviewed = dict(record)
    reviewed["status"] = status
    reviewed["audit_status"] = audit_status
    reviewed["updated"] = _today()
    reviewed["reviewed_at"] = _today()
    if reason is not None:
        reviewed["review_reason"] = reason
    return reviewed


def accept_candidate(args):
    root, records = _records_by_id(args.root)
    candidate_item = _require_candidate(records, args.id)
    candidate = dict(candidate_item["record"])
    action = candidate.get("candidate_action")
    requested_action = candidate.get("requested_action", action)
    target_id = candidate.get("target_id")
    target_item = records.get(target_id) if target_id else None
    run_id = next(
        (
            entry["run_id"] for entry in reversed(_journal_entries(root))
            if candidate["id"] in entry.get("proposal_ids", [])
        ),
        uuid.uuid4().hex,
    )
    try:
        _check_source(root, candidate)
        if action in {"UPDATE", "DEPRECATE"}:
            if target_item is None:
                raise DistillError("stale_target", "target memory no longer exists", False, "regenerate_proposal")
            expected_status = candidate.get("expected_target_status")
            expected_sha256 = candidate.get("expected_target_sha256")
            current_sha256 = hashlib.sha256(target_item["path"].read_bytes()).hexdigest()
            if target_item["record"].get("status") != expected_status or current_sha256 != expected_sha256:
                raise DistillError("stale_target", "target memory changed after proposal", False, "regenerate_proposal")

        operations = []
        expected = []
        if requested_action == "context_transition":
            try:
                new_record = json.loads(candidate["new_record_json"])
                new_path = root / TYPE_DIRS[new_record["type"]] / f"{new_record['id']}-{safe_slug(new_record['title'])}.md"
                valid_until = (date.fromisoformat(candidate["effective_date"]) - timedelta(days=1)).isoformat()
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DistillError("invalid_schema", "transition proposal is incomplete", False, "regenerate_proposal") from exc
            if new_path.exists():
                raise DistillError("stale_target", "planned context path already exists", False, "regenerate_proposal")
            target = dict(target_item["record"])
            target["status"] = "deprecated"
            target["updated"] = _today()
            target["valid_until"] = valid_until
            target["superseded_by"] = _merge_unique(_list(target.get("superseded_by")), [new_record["id"]])
            new_record["status"] = "active"
            new_record["supersedes"] = _merge_unique(_list(new_record.get("supersedes")), [target["id"]])
            final = _reviewed(candidate, "archived", "accepted")
            operations.extend(
                [
                    (target_item["path"], render_existing_memory(target_item["path"], target)),
                    (new_path, render_memory(new_record)),
                    (candidate_item["path"], render_existing_memory(candidate_item["path"], final)),
                ]
            )
            expected.extend([(target_item["path"], target), (new_path, new_record), (candidate_item["path"], final)])
        elif action == "ADD":
            final = _reviewed(candidate, "active", "accepted")
            operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))
            expected.append((candidate_item["path"], final))
        elif requested_action == "merge":
            target = dict(target_item["record"])
            for field in SAFE_MERGE_LISTS:
                target[field] = _merge_unique(_list(target.get(field)), _list(candidate.get(field)))
            target["updated"] = _today()
            final = _reviewed(candidate, "archived", "accepted")
            operations.extend(
                [
                    (target_item["path"], render_existing_memory(target_item["path"], target)),
                    (candidate_item["path"], render_existing_memory(candidate_item["path"], final)),
                ]
            )
            expected.extend([(target_item["path"], target), (candidate_item["path"], final)])
        elif requested_action == "support":
            target = dict(target_item["record"])
            target["source_refs"] = _merge_unique(_list(target.get("source_refs")), _list(candidate.get("source_refs")) + _list(candidate.get("evidence")))
            target["updated"] = _today()
            final = _reviewed(candidate, "archived", "accepted")
            operations.extend(
                [
                    (target_item["path"], render_existing_memory(target_item["path"], target)),
                    (candidate_item["path"], render_existing_memory(candidate_item["path"], final)),
                ]
            )
            expected.extend([(target_item["path"], target), (candidate_item["path"], final)])
        elif action in {"UPDATE", "DEPRECATE"}:
            target = dict(target_item["record"])
            target["status"] = "deprecated"
            target["updated"] = _today()
            target["superseded_by"] = _merge_unique(_list(target.get("superseded_by")), [candidate["id"]])
            final = _reviewed(candidate, "active", "accepted")
            final["supersedes"] = _merge_unique(_list(final.get("supersedes")), [target["id"]])
            operations.extend(
                [
                    (target_item["path"], render_existing_memory(target_item["path"], target)),
                    (candidate_item["path"], render_existing_memory(candidate_item["path"], final)),
                ]
            )
            expected.extend([(target_item["path"], target), (candidate_item["path"], final)])
        elif action == "REVIEW_REQUIRED":
            final = _reviewed(candidate, "conflict", "conflict")
            operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))
            expected.append((candidate_item["path"], final))
        else:
            final = _reviewed(candidate, "archived", "rejected", "discarded by candidate action")
            operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))
            expected.append((candidate_item["path"], final))

        replacements = {path: record for path, record in expected}
        hypothetical = [
            {**item, "record": replacements.get(item["path"], item["record"])}
            for item in records.values()
        ]
        existing_paths = {item["path"] for item in records.values()}
        hypothetical.extend(
            {"path": path, "relative_path": path.relative_to(root).as_posix(), "record": record}
            for path, record in expected if path not in existing_paths
        )
        validation_errors = _validate_hypothetical_records(root, hypothetical)
        if validation_errors:
            message = "; ".join(f"{path}: {error}" for path, error in validation_errors)
            raise DistillError("invalid_transition", message, False, "regenerate_proposal")

        _apply_files_and_index(root, args.state_dir, operations, expected)
    except DistillError as exc:
        _append_journal(
            root, run_id=run_id, status=exc.feedback["code"], iteration=1, max_iterations=2,
            input_hash=hashlib.sha256(candidate["id"].encode()).hexdigest(),
            proposal_ids=[candidate["id"]], errors=[exc.feedback], stop_reason=exc.feedback["code"],
            next_action=exc.feedback["next_action"],
            resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
        )
        raise
    _append_journal(
        root, run_id=run_id, status="completed", iteration=1, max_iterations=2,
        input_hash=hashlib.sha256(candidate["id"].encode()).hexdigest(),
        proposal_ids=[candidate["id"]], stop_reason="completed", next_action=None,
        resume_command=f"memory_distill.py resume --root {root} --run-id {run_id}",
    )
    return {"candidate_id": candidate["id"], "action": action, "run_id": run_id, "status": "completed"}


def reject_candidate(args):
    root, records = _records_by_id(args.root)
    candidate_item = _require_candidate(records, args.id)
    final = _reviewed(candidate_item["record"], "archived", "rejected", args.reason)
    _replace_transaction([(candidate_item["path"], render_existing_memory(candidate_item["path"], final))], [])
    return {"candidate_id": args.id, "status": "archived", "audit_status": "rejected"}


def review_candidates(args):
    root, records = _records_by_id(args.root)
    rows = [
        {
            "id": item["record"]["id"],
            "action": item["record"].get("candidate_action"),
            "target_id": item["record"].get("target_id"),
            "title": item["record"]["title"],
            "relative_path": item["relative_path"],
        }
        for item in records.values()
        if item["record"].get("status") == "candidate"
    ]
    rows.sort(key=lambda row: row["id"])
    return rows


def build_parser():
    parser = argparse.ArgumentParser(description="Review distilled candidate memories.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Write a candidate memory for review.")
    apply_parser.add_argument("--root", required=True)
    apply_parser.add_argument("--state-dir", default=str(default_state_dir()))
    apply_parser.add_argument("--action", required=True, choices=sorted(REVIEW_ACTIONS))
    apply_parser.add_argument("--confirmed", action="store_true")
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument("--max-iterations", type=int, default=2)
    apply_parser.add_argument("--type", required=True, choices=TYPE_CHOICES)
    apply_parser.add_argument("--title", required=True)
    apply_parser.add_argument("--scope", required=True, choices=SCOPE_CHOICES)
    apply_parser.add_argument("--workspace", required=True, choices=WORKSPACE_CHOICES)
    apply_parser.add_argument("--confidentiality", required=True, choices=CONFIDENTIALITY_CHOICES)
    apply_parser.add_argument("--source", required=True)
    apply_parser.add_argument("--confidence", default="inferred", choices=CONFIDENCE_CHOICES)
    apply_parser.add_argument("--content", required=True)
    apply_parser.add_argument("--target-id")
    apply_parser.add_argument("--project")
    apply_parser.add_argument("--context-id")
    apply_parser.add_argument("--source-id")
    apply_parser.add_argument("--source-path")
    apply_parser.add_argument("--source-sha256")
    apply_parser.add_argument("--evidence", nargs="*")
    apply_parser.add_argument("--source-refs", nargs="*")
    apply_parser.add_argument("--relations", nargs="*")
    apply_parser.add_argument("--tags", nargs="*")

    review_parser = subparsers.add_parser("review", help="List candidate memories.")
    review_parser.add_argument("--root", required=True)
    review_parser.add_argument("--json", action="store_true")

    accept_parser = subparsers.add_parser("accept", help="Accept a candidate memory.")
    accept_parser.add_argument("--root", required=True)
    accept_parser.add_argument("--state-dir", default=str(default_state_dir()))
    accept_parser.add_argument("--id", required=True)

    reject_parser = subparsers.add_parser("reject", help="Reject a candidate memory.")
    reject_parser.add_argument("--root", required=True)
    reject_parser.add_argument("--id", required=True)
    reject_parser.add_argument("--reason", required=True)

    resume_parser = subparsers.add_parser("resume", help="Read the durable state of a distill run.")
    resume_parser.add_argument("--root", required=True)
    resume_parser.add_argument("--run-id", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "apply":
            summary = apply_candidate(args)
            if summary.get("candidate_id"):
                print(f"candidate_id: {summary['candidate_id']}")
            if summary.get("path"):
                print(f"path: {summary['path']}")
            print(f"Run: {summary['run_id']}")
            print(f"Action: {summary['action']}")
            print(f"Status: {summary['status']}")
        elif args.command == "review":
            rows = review_candidates(args)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(f"{row['id']} {row['action']} {row['title']}")
                print(f"Candidates: {len(rows)}")
        elif args.command == "accept":
            summary = accept_candidate(args)
            print(f"Accepted: {summary['candidate_id']}")
            print(f"Action: {summary['action']}")
        elif args.command == "resume":
            summary = resume_run(args)
            print(f"Run: {summary['run_id']}")
            print(f"Status: {summary['status']}")
        else:
            summary = reject_candidate(args)
            print(f"Rejected: {summary['candidate_id']}")
        return 0
    except DistillError as exc:
        print(json.dumps(exc.feedback, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
