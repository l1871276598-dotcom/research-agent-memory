import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import date

from memory import (
    CONFIDENTIALITY_CHOICES,
    CONFIDENCE_CHOICES,
    SCOPE_CHOICES,
    TYPE_CHOICES,
    TYPE_DIRS,
    WORKSPACE_CHOICES,
    _replace_transaction,
    _require_data_root,
    atomic_write_text,
    collect_validated_records,
    render_existing_memory,
    render_memory,
    safe_slug,
)


REVIEW_ACTIONS = {"create", "merge", "support", "supersede", "conflict", "discard"}
SAFE_MERGE_LISTS = ("source_refs", "tags", "relations")


def _today():
    return date.today().isoformat()


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


def apply_candidate(args):
    root = _require_data_root(args.root)
    if args.action not in REVIEW_ACTIONS:
        raise ValueError("invalid candidate action")
    if args.action in {"merge", "support", "supersede", "conflict"} and not args.target_id:
        raise ValueError("target-id is required for this action")
    if args.confidentiality in {"internal", "restricted"} and args.workspace != "work":
        raise ValueError("confidentiality internal or restricted requires workspace work")

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
        "candidate_action": args.action,
        "content": args.content,
        "tags": args.tags or [],
    }
    for field in ["target_id", "project", "context_id", "source_id", "source_path", "source_sha256"]:
        value = getattr(args, field)
        if value:
            record[field] = value
    for field in ["evidence", "source_refs", "relations"]:
        value = getattr(args, field)
        if value:
            record[field] = value

    atomic_write_text(path, render_memory(record))
    return {"candidate_id": candidate_id, "path": path.relative_to(root).as_posix()}


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
        raise ValueError("source file not found")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source_sha256:
        raise ValueError("source hash changed")


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
    _check_source(root, candidate)
    action = candidate.get("candidate_action")
    target_id = candidate.get("target_id")
    target_item = records.get(target_id) if target_id else None
    if action in {"merge", "support", "supersede", "conflict"} and target_item is None:
        raise ValueError("target memory not found")

    operations = []
    if action == "create":
        final = _reviewed(candidate, "active", "accepted")
        operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))
    elif action == "merge":
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
    elif action == "support":
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
    elif action == "supersede":
        target = dict(target_item["record"])
        target["status"] = "historical"
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
    elif action == "conflict":
        final = _reviewed(candidate, "conflict", "conflict")
        operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))
    else:
        final = _reviewed(candidate, "archived", "rejected", "discarded by candidate action")
        operations.append((candidate_item["path"], render_existing_memory(candidate_item["path"], final)))

    _replace_transaction(operations, [])
    return {"candidate_id": candidate["id"], "action": action}


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
    apply_parser.add_argument("--action", required=True, choices=sorted(REVIEW_ACTIONS))
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
    accept_parser.add_argument("--id", required=True)

    reject_parser = subparsers.add_parser("reject", help="Reject a candidate memory.")
    reject_parser.add_argument("--root", required=True)
    reject_parser.add_argument("--id", required=True)
    reject_parser.add_argument("--reason", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "apply":
            summary = apply_candidate(args)
            print(f"candidate_id: {summary['candidate_id']}")
            print(f"path: {summary['path']}")
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
        else:
            summary = reject_candidate(args)
            print(f"Rejected: {summary['candidate_id']}")
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
