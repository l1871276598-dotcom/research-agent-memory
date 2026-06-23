import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import memory


ACTION_CHOICES = {"create", "merge", "support", "supersede", "conflict", "discard"}


def _today(value=None):
    return date.fromisoformat(value) if value else date.today()


def _state(root, state_dir):
    root = memory._require_data_root(root)
    state, db = memory.check_state_dir(root, state_dir or memory.default_state_dir())
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init first.")
    return root, state, db


def _db_connect(db):
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _recent_items(root):
    _, records, errors, _ = memory.collect_validated_records(root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Distillation aborted because validation failed."]))
    return [item for item in records if item["record"].get("schema") == "recent/v1"]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _audit_by_recent(conn):
    return {
        row[0]: dict(zip(memory.AUDIT_COLUMNS, row))
        for row in conn.execute("SELECT " + ", ".join(memory.AUDIT_COLUMNS) + " FROM distillation_audit")
    }


def prepare(args):
    root, state, db = _state(args.root, Path(args.state_dir))
    today = _today(args.today)
    tasks_dir = state / "distillation" / "tasks"
    tasks = []
    with _db_connect(db) as conn:
        audit = _audit_by_recent(conn)
    existing = [
        {"relative_path": item["relative_path"], "record": item["record"]}
        for item in memory.collect_validated_records(root)[1]
        if item["record"].get("schema") != "recent/v1"
    ]
    for item in _recent_items(root):
        record = item["record"]
        retention = memory._real_date(record.get("retention_until"))
        current_sha = _sha(item["path"])
        audit_row = audit.get(record["id"])
        if retention is None or retention > today:
            continue
        if str(record.get("protected", "false")).lower() == "true":
            continue
        if audit_row and audit_row.get("source_sha256") == current_sha and audit_row.get("status") in {"pending_delete", "deleted"}:
            continue
        task_id = f"distill-{record['id']}-{uuid.uuid4().hex[:8]}"
        task_dir = tasks_dir / task_id
        if not args.dry_run:
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "source.md").write_text(item["path"].read_text(encoding="utf-8"), encoding="utf-8")
            (task_dir / "existing_memories.json").write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            (task_dir / "result_schema.json").write_text(json.dumps({"schema": "distillation-result/v1"}, indent=2), encoding="utf-8")
            (task_dir / "instructions.md").write_text(
                "Return distillation_result.json only. Do not modify source memory or raw files.\n",
                encoding="utf-8",
            )
            (task_dir / "task.json").write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "source_id": record["id"],
                        "source_sha256": current_sha,
                        "relative_path": item["relative_path"],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        tasks.append({"task_id": task_id, "task_dir": str(task_dir), "source_id": record["id"], "source_sha256": current_sha})
    return {"tasks": tasks, "count": len(tasks), "dry_run": args.dry_run}


def run_task(args):
    task_dir = Path(args.task_dir).resolve(strict=True)
    result_path = task_dir / "distillation_result.json"
    try:
        result = subprocess.run(
            [args.codex_bin],
            cwd=task_dir,
            text=True,
            capture_output=True,
            timeout=args.timeout,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        result = exc
        timed_out = True
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    code = getattr(result, "returncode", 124 if timed_out else 1)
    (task_dir / "codex_stdout.log").write_text(stdout, encoding="utf-8")
    (task_dir / "codex_stderr.log").write_text(stderr, encoding="utf-8")
    (task_dir / "codex_exit_code.txt").write_text(str(code) + "\n", encoding="utf-8")
    if timed_out:
        raise ValueError("Codex distillation timed out.")
    if code != 0:
        raise ValueError("Codex distillation failed.")
    if not result_path.exists():
        raise ValueError("Codex distillation result is missing.")
    try:
        json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Codex distillation result is invalid JSON.") from exc
    return {"task_dir": str(task_dir), "exit_code": code, "result": str(result_path)}


def _load_task(task_dir):
    task_dir = Path(task_dir).resolve(strict=True)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    result = json.loads((task_dir / "distillation_result.json").read_text(encoding="utf-8"))
    return task_dir, task, result


def _validate_result(task, result):
    if result.get("schema") != "distillation-result/v1":
        raise ValueError("Invalid distillation result schema.")
    if result.get("source_id") != task["source_id"]:
        raise ValueError("Distillation source_id does not match task.")
    if result.get("source_sha256") != task["source_sha256"]:
        raise ValueError("Distillation source_sha256 does not match task.")
    if not isinstance(result.get("candidates"), list):
        raise ValueError("Distillation candidates must be a list.")
    if not isinstance(result.get("unresolved_conflicts", []), list):
        raise ValueError("Distillation unresolved_conflicts must be a list.")
    for candidate in result["candidates"]:
        if candidate.get("action") not in ACTION_CHOICES:
            raise ValueError("Distillation candidate action is invalid.")
        for relation in candidate.get("relations", []):
            name, target = memory._relation_parts(relation)
            if name not in memory.RELATION_CHOICES or not target:
                raise ValueError("Distillation candidate relation is invalid.")


def _new_memory_path(root, memory_type, title):
    target_dir = root / memory.TYPE_DIRS[memory_type]
    target_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat().replace("-", "")
    while True:
        memory_id = f"{memory_type}-{today}-{uuid.uuid4().hex[:8]}"
        path = target_dir / f"{memory_id}-{memory.safe_slug(title)}.md"
        if not path.exists():
            return memory_id, path


def _candidate_record(root, source_record, candidate):
    action = candidate["action"]
    memory_type = candidate.get("type", "principle")
    if memory_type not in memory.TYPE_DIRS:
        raise ValueError("Distillation candidate type is invalid.")
    title = candidate.get("title") or source_record["title"]
    memory_id, path = _new_memory_path(root, memory_type, title)
    today = date.today().isoformat()
    status = "conflict" if action == "conflict" else "active"
    relations = list(candidate.get("relations", []))
    relations.append(f"derived_from:{source_record['id']}")
    record = {
        "schema": "memory/v2",
        "id": memory_id,
        "type": memory_type,
        "title": title,
        "created": today,
        "updated": today,
        "status": status,
        "scope": candidate.get("scope", "global"),
        "workspace": candidate.get("workspace", "personal"),
        "confidentiality": candidate.get("confidentiality", "personal"),
        "source": "codex_distillation",
        "confidence": candidate.get("confidence", "inferred"),
        "subject": candidate.get("subject", title),
        "predicate": candidate.get("predicate", "states"),
        "object": candidate.get("object", candidate.get("content", title)[:80] or title),
        "relations": relations,
        "source_refs": [source_record["id"]],
        "tags": candidate.get("tags", []),
        "content": candidate.get("content", source_record.get("content", "")),
    }
    if record["scope"] == "project" and candidate.get("project"):
        record["project"] = candidate["project"]
    return path, record


def _find_recent(root, source_id):
    for item in _recent_items(root):
        if item["record"].get("id") == source_id:
            return item
    raise ValueError("Source recent memory is missing.")


def _find_memory_item(root, memory_id):
    for item in memory.collect_validated_records(root)[1]:
        if item["record"].get("id") == memory_id:
            return item
    return None


def _append_unique(record, field, values):
    current = list(record.get(field, [])) if isinstance(record.get(field, []), list) else []
    for value in values:
        if value not in current:
            current.append(value)
    record[field] = current


def _merge_existing(root, source_record, candidate, mode):
    target_id = candidate.get("target_id")
    if not target_id:
        raise ValueError(f"Distillation {mode} candidate requires target_id.")
    item = _find_memory_item(root, target_id)
    if not item or item["record"].get("schema") == "recent/v1":
        raise ValueError(f"Distillation {mode} target is missing.")
    record = dict(item["record"])
    _append_unique(record, "source_refs", [source_record["id"]])
    relation = "supports" if mode == "support" else "derived_from"
    _append_unique(record, "relations", [f"{relation}:{source_record['id']}"])
    record["updated"] = date.today().isoformat()
    return item["path"], record, item["path"].read_text(encoding="utf-8")


def _supersede_existing(root, source_record, candidate):
    target_id = candidate.get("target_id")
    if not target_id:
        raise ValueError("Distillation supersede candidate requires target_id.")
    item = _find_memory_item(root, target_id)
    if not item or item["record"].get("schema") == "recent/v1":
        raise ValueError("Distillation supersede target is missing.")
    new_path, new_record = _candidate_record(root, source_record, candidate)
    _append_unique(new_record, "relations", [f"supersedes:{target_id}"])
    old_record = dict(item["record"])
    old_record["status"] = candidate.get("old_status", "historical")
    old_record["updated"] = date.today().isoformat()
    _append_unique(old_record, "source_refs", [source_record["id"]])
    _append_unique(old_record, "superseded_by", [new_record["id"]])
    _append_unique(old_record, "relations", [f"supports:{source_record['id']}", f"superseded_by:{new_record['id']}"])
    return item["path"], old_record, item["path"].read_text(encoding="utf-8"), new_path, new_record


def apply(args):
    root, state, db = _state(args.root, Path(args.state_dir))
    with memory.write_lock(root, state):
        task_dir, task, result = _load_task(args.task_dir)
        _validate_result(task, result)
        recent_item = _find_recent(root, task["source_id"])
        if _sha(recent_item["path"]) != task["source_sha256"]:
            raise ValueError("Source recent memory changed after prepare.")
        created = []
        updated = []
        has_conflict = bool(result.get("unresolved_conflicts"))
        for candidate in result["candidates"]:
            action = candidate["action"]
            if action == "discard":
                continue
            if action in {"merge", "support"}:
                updated.append(_merge_existing(root, recent_item["record"], candidate, action))
                continue
            if action == "supersede":
                old_path, old_record, old_text, new_path, new_record = _supersede_existing(root, recent_item["record"], candidate)
                updated.append((old_path, old_record, old_text))
                created.append((new_path, new_record))
                continue
            if action == "conflict":
                has_conflict = True
            path, record = _candidate_record(root, recent_item["record"], candidate)
            created.append((path, record))
        try:
            for path, record, _old_text in updated:
                memory.atomic_write_text(path, memory.render_existing_memory(path, record))
            for path, record in created:
                memory.atomic_write_text(path, memory.render_memory(record))
            count, errors = memory.validate_store(root)
            if errors:
                raise ValueError("Distillation apply produced invalid memory.")
            memory.index_store(type("Args", (), {"root": str(root), "state_dir": str(state), "dry_run": False})())
        except Exception:
            for path, _ in created:
                path.unlink(missing_ok=True)
            for path, _record, old_text in updated:
                memory.atomic_write_text(path, old_text)
            try:
                memory.index_store(type("Args", (), {"root": str(root), "state_dir": str(state), "dry_run": False})())
            except Exception:
                pass
            raise
        status = "conflict" if has_conflict else "pending_delete"
        today = _today(args.today)
        delete_after = None if has_conflict else (today + timedelta(days=memory.DELETION_GRACE_DAYS)).isoformat()
        with _db_connect(db) as conn:
            conn.execute(
                """
                INSERT INTO distillation_audit(recent_id, relative_path, source_sha256, status, result_json, distilled_at, delete_after, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recent_id) DO UPDATE SET
                  relative_path = excluded.relative_path,
                  source_sha256 = excluded.source_sha256,
                  status = excluded.status,
                  result_json = excluded.result_json,
                  distilled_at = excluded.distilled_at,
                  delete_after = excluded.delete_after,
                  deleted_at = NULL,
                  error = excluded.error
                """,
                (
                    task["source_id"],
                    recent_item["relative_path"],
                    task["source_sha256"],
                    status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    today.isoformat(),
                    delete_after,
                    None,
                ),
            )
            conn.commit()
        return {"applied": len(created), "status": status, "delete_after": delete_after}


def _source_raw_exists(root, record):
    source = record.get("source_path")
    return bool(source) and (root / source).exists()


def purge(args):
    root, state, db = _state(args.root, Path(args.state_dir))
    today = _today(args.today)
    deleted = 0
    skipped = 0
    with memory.write_lock(root, state):
        with _db_connect(db) as conn:
            rows = [
                dict(zip(memory.AUDIT_COLUMNS, row))
                for row in conn.execute(
                    "SELECT " + ", ".join(memory.AUDIT_COLUMNS) + " FROM distillation_audit WHERE status = 'pending_delete'"
                )
            ]
        for row in rows:
            delete_after = memory._real_date(row.get("delete_after"))
            path = root / row["relative_path"]
            if delete_after is None or delete_after > today or not path.exists():
                skipped += 1
                continue
            record, _, errors = memory.parse_memory_document(path)
            if errors or not record:
                skipped += 1
                continue
            if str(record.get("protected", "false")).lower() == "true":
                skipped += 1
                continue
            if _sha(path) != row["source_sha256"]:
                with _db_connect(db) as conn:
                    conn.execute("UPDATE distillation_audit SET status = ?, error = ? WHERE recent_id = ?", ("stale", "recent hash changed", row["recent_id"]))
                    conn.commit()
                skipped += 1
                continue
            if not _source_raw_exists(root, record):
                skipped += 1
                continue
            if memory.validate_store(root)[1]:
                skipped += 1
                continue
            result = json.loads(row.get("result_json") or "{}")
            if result.get("unresolved_conflicts"):
                skipped += 1
                continue
            path.unlink()
            memory.index_store(type("Args", (), {"root": str(root), "state_dir": str(state), "dry_run": False})())
            with _db_connect(db) as conn:
                conn.execute(
                    "UPDATE distillation_audit SET status = ?, deleted_at = ?, error = NULL WHERE recent_id = ?",
                    ("deleted", today.isoformat(), row["recent_id"]),
                )
                conn.commit()
            deleted += 1
    return {"deleted": deleted, "skipped": skipped}


def status(args):
    _, _, db = _state(args.root, Path(args.state_dir))
    with _db_connect(db) as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM distillation_audit GROUP BY status").fetchall()
    return {"audit": dict(rows)}


def daily(args):
    prepared = prepare(args)
    return {"prepared": prepared["count"], "purge": purge(args)}


def build_parser():
    parser = argparse.ArgumentParser(description="Codex memory distillation lifecycle.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["prepare", "apply", "purge", "daily", "status"]:
        item = sub.add_parser(name)
        item.add_argument("--root", required=True)
        item.add_argument("--state-dir", required=True)
        item.add_argument("--today")
        item.add_argument("--json", action="store_true", dest="json_output")
        if name == "prepare":
            item.add_argument("--dry-run", action="store_true")
        if name == "apply":
            item.add_argument("--task-dir", required=True)
    run = sub.add_parser("run")
    run.add_argument("--task-dir", required=True)
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--timeout", type=int, default=120)
    run.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            summary = prepare(args)
        elif args.command == "run":
            summary = run_task(args)
        elif args.command == "apply":
            summary = apply(args)
        elif args.command == "purge":
            summary = purge(args)
        elif args.command == "daily":
            summary = daily(args)
        elif args.command == "status":
            summary = status(args)
        else:
            parser.error("unknown command")
            return 2
        if getattr(args, "json_output", False):
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in summary.items():
                print(f"{key}: {value}")
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
