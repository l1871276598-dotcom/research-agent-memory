import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path


DIRECTORIES = [
    "memory/profile",
    "memory/contexts",
    "memory/transitions",
    "memory/principles",
    "memory/projects",
    "memory/decisions",
    "memory/procedures",
    "memory/sessions",
    "literature/inbox",
    "literature/pdf",
    "literature/notes",
    "literature/journals",
    "manuscripts/current",
    "manuscripts/evidence",
    "manuscripts/archive",
    "exports/database_snapshots",
    "backups",
]

TYPE_CHOICES = [
    "profile",
    "context",
    "context_transition",
    "principle",
    "project",
    "decision",
    "procedure",
    "session",
]
STATUS_CHOICES = ["active", "historical", "deprecated", "candidate", "conflict", "archived"]
SCOPE_CHOICES = ["global", "context", "project"]
WORKSPACE_CHOICES = ["personal", "work"]
CONFIDENTIALITY_CHOICES = ["public", "personal", "internal", "restricted"]
CONFIDENCE_CHOICES = ["confirmed", "inferred", "uncertain"]

TYPE_DIRS = {
    "profile": "memory/profile",
    "context": "memory/contexts",
    "context_transition": "memory/transitions",
    "principle": "memory/principles",
    "project": "memory/projects",
    "decision": "memory/decisions",
    "procedure": "memory/procedures",
    "session": "memory/sessions",
}
DIR_TYPES = {value: key for key, value in TYPE_DIRS.items()}
REQUIRED_FIELDS = [
    "id",
    "type",
    "title",
    "created",
    "updated",
    "status",
    "scope",
    "workspace",
    "confidentiality",
    "source",
    "confidence",
    "content",
]
ALLOWED_FIELDS = set(REQUIRED_FIELDS) | {
    "context_id",
    "project",
    "valid_from",
    "valid_until",
    "supersedes",
    "superseded_by",
    "tags",
    "from_context",
    "to_context",
    "effective_date",
    "reason",
}
LIST_FIELDS = {"tags", "supersedes", "superseded_by"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")

FILES = {
    ".research-agent-root": json.dumps(
        {"format_version": 1, "type": "research-agent-data-root"},
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    "exports/index_manifest.json": json.dumps(
        {"format_version": 1, "records": []},
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    "literature/literature_matrix.csv": (
        "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags\n"
    ),
}


def _repository_root():
    for path in Path(__file__).resolve().parents:
        if (path / ".git").exists():
            return path
    return None


def init_store(root):
    root = Path(root).expanduser().resolve(strict=False)
    repo_root = _repository_root()
    if repo_root is not None and root == repo_root.resolve():
        raise ValueError("数据目录不能等于代码仓库目录。")

    created_dirs = 0
    created_files = 0
    existing_items = 0

    if root.exists():
        existing_items += 1
    else:
        created_dirs += 1
    root.mkdir(parents=True, exist_ok=True)

    for relative in DIRECTORIES:
        path = root / relative
        if path.exists():
            existing_items += 1
        else:
            created_dirs += 1
            path.mkdir(parents=True, exist_ok=True)

    for relative, content in FILES.items():
        path = root / relative
        if path.exists():
            existing_items += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created_files += 1

    return root, created_dirs, created_files, existing_items


def _require_data_root(root):
    root = Path(root).expanduser().resolve(strict=False)
    marker = root / ".research-agent-root"
    hint = "请先执行：python3 src/memory.py init --root PATH"
    if not root.exists() or not root.is_dir() or not marker.exists():
        raise ValueError(hint)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{hint}") from exc
    if data.get("type") != "research-agent-data-root" or data.get("format_version") != 1:
        raise ValueError(hint)
    return root


def _check_date(value, name):
    if value is None:
        return
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} 必须符合 YYYY-MM-DD。")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须符合 YYYY-MM-DD。") from exc


def validate_add_args(args):
    if args.type == "context" and args.scope != "context":
        raise ValueError("type 为 context 时 --scope 必须是 context。")
    if args.type == "context_transition" and args.scope != "context":
        raise ValueError("type 为 context_transition 时 --scope 必须是 context。")
    if args.type == "context_transition":
        missing = [
            name
            for name, value in [
                ("--from-context", args.from_context),
                ("--to-context", args.to_context),
                ("--effective-date", args.effective_date),
                ("--reason", args.reason),
            ]
            if not value
        ]
        if missing:
            raise ValueError("context_transition 缺少必填参数：" + ", ".join(missing))
    if args.type == "project" and args.scope != "project":
        raise ValueError("type 为 project 时 --scope 必须是 project。")
    if args.type == "project" and not args.project:
        raise ValueError("type 为 project 时必须提供 --project。")
    if args.scope == "context" and args.type != "context_transition" and not args.context_id:
        raise ValueError("scope 为 context 时必须提供 --context-id。")
    if args.scope == "project" and not args.project:
        raise ValueError("scope 为 project 时必须提供 --project。")
    if args.confidentiality in {"internal", "restricted"} and args.workspace != "work":
        raise ValueError("confidentiality 为 internal 或 restricted 时 workspace 必须是 work。")
    for name in ("valid_from", "valid_until", "effective_date"):
        _check_date(getattr(args, name), name.replace("_", "-"))


def safe_slug(title):
    parts = []
    previous_hyphen = False
    for char in title:
        if char.isalnum():
            parts.append(char)
            previous_hyphen = False
        elif char.isspace() or char in "-_/\\:" or ord(char) < 32:
            if not previous_hyphen:
                parts.append("-")
                previous_hyphen = True
        elif not previous_hyphen:
            parts.append("-")
            previous_hyphen = True
    slug = "".join(parts).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug or "memory"


def _quoted(value):
    return json.dumps(value, ensure_ascii=False)


def render_front_matter(record):
    lines = ["---"]
    for field in [
        "id",
        "type",
        "title",
        "created",
        "updated",
        "status",
        "scope",
        "workspace",
        "confidentiality",
        "source",
        "confidence",
        "context_id",
        "project",
        "valid_from",
        "valid_until",
        "from_context",
        "to_context",
        "effective_date",
        "reason",
    ]:
        if field in record:
            lines.append(f"{field}: {_quoted(record[field])}")

    for field in ["supersedes", "superseded_by", "tags"]:
        if field not in record:
            continue
        values = record.get(field, [])
        lines.append(f"{field}:")
        for value in values:
            lines.append(f"  - {_quoted(value)}")
        if not values:
            lines[-1] = f"{field}: []"

    lines.append("content: |-")
    content_lines = record["content"].splitlines() or [""]
    for line in content_lines:
        lines.append(f"  {line}")
    lines.append("---")
    return "\n".join(lines)


def render_memory(record):
    lines = [
        render_front_matter(record),
        "",
        f"# {record['title']}",
        "",
        "该记忆的结构化内容保存在 front matter 的 content 字段中。",
        "",
    ]
    return "\n".join(lines)


def render_existing_memory(path, record):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\n") == "---":
            end = index
            break
    body = "".join(lines[end + 1 :]) if end is not None else ""
    return render_front_matter(record) + "\n" + body


def add_memory(args):
    validate_add_args(args)
    root = _require_data_root(args.root)
    target_dir = root / TYPE_DIRS[args.type]
    target_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    day_id = today.replace("-", "")
    record = {
        "type": args.type,
        "title": args.title,
        "created": today,
        "updated": today,
        "status": args.status,
        "scope": args.scope,
        "workspace": args.workspace,
        "confidentiality": args.confidentiality,
        "source": args.source,
        "confidence": args.confidence,
        "content": args.content,
        "tags": args.tags or [],
    }
    for field in [
        "context_id",
        "project",
        "valid_from",
        "valid_until",
        "from_context",
        "to_context",
        "effective_date",
        "reason",
    ]:
        value = getattr(args, field)
        if value:
            record[field] = value

    slug = safe_slug(args.title)
    while True:
        memory_id = f"{args.type}-{day_id}-{uuid.uuid4().hex[:8]}"
        target = target_dir / f"{memory_id}-{slug}.md"
        if not target.exists():
            break

    record["id"] = memory_id
    tmp = target_dir / f".{memory_id}.tmp"
    try:
        tmp.write_text(render_memory(record), encoding="utf-8")
        if target.exists():
            tmp.unlink(missing_ok=True)
            return add_memory(args)
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise

    return memory_id, target.relative_to(root), args.type, args.status


def _parse_scalar(value):
    if value.startswith('"'):
        parsed = json.loads(value)
        if not isinstance(parsed, str):
            raise ValueError("quoted value must be a string")
        return parsed
    return value


def _parse_list_item(value):
    item = _parse_scalar(value)
    if not isinstance(item, str):
        raise ValueError("list item must be a string")
    return item


def parse_front_matter(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return None, ["file is not valid UTF-8"]
    except OSError as exc:
        return None, [f"cannot read file: {exc}"]

    if not lines or lines[0] != "---":
        return None, ["missing opening front matter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, ["unclosed front matter"]

    record = {}
    errors = []
    front_matter = lines[1:end]
    i = 0
    while i < len(front_matter):
        line = front_matter[i]
        if not line:
            i += 1
            continue
        if line.startswith("  - "):
            errors.append("list item appears outside a list field")
            i += 1
            continue
        if line.startswith(" "):
            errors.append("unsupported nested object or indentation")
            i += 1
            continue
        if ":" not in line:
            errors.append("front matter line missing colon")
            i += 1
            continue

        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.lstrip()
        if key in record:
            errors.append(f"duplicate key: {key}")
        if raw in {"|-", "|"}:
            values = []
            i += 1
            while i < len(front_matter):
                block_line = front_matter[i]
                if block_line.startswith("  "):
                    values.append(block_line[2:])
                    i += 1
                    continue
                if block_line.startswith(" "):
                    errors.append(f"{key} indentation error")
                    i += 1
                    continue
                break
            record[key] = "\n".join(values)
            continue
        if raw == "":
            values = []
            i += 1
            while i < len(front_matter) and front_matter[i].startswith("  - "):
                try:
                    values.append(_parse_list_item(front_matter[i][4:].strip()))
                except (json.JSONDecodeError, ValueError):
                    errors.append(f"cannot parse list item for {key}")
                i += 1
            if i < len(front_matter) and front_matter[i].startswith(" "):
                errors.append(f"unsupported nested object for {key}")
                i += 1
            if not values and key not in LIST_FIELDS:
                errors.append(f"empty value is only supported for list fields: {key}")
            record[key] = values
            continue
        if raw == "[]":
            record[key] = []
        else:
            try:
                record[key] = _parse_scalar(raw)
            except json.JSONDecodeError:
                errors.append(f"cannot parse quoted string for {key}")
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
        i += 1

    return record, errors


def _real_date(value):
    if not isinstance(value, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_record(record, path, rel_path, expected_type):
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in record:
            errors.append(f"missing required field: {field}")
    for field in record:
        if field not in ALLOWED_FIELDS:
            errors.append(f"unknown field: {field}")

    memory_id = record.get("id")
    if not isinstance(memory_id, str):
        errors.append("id must be a string")
    elif not (3 <= len(memory_id) <= 128) or not ID_PATTERN.fullmatch(memory_id):
        errors.append("id does not match required pattern")
    elif not path.name.startswith(f"{memory_id}-"):
        errors.append("filename must start with id-")

    for field, choices in [
        ("type", TYPE_CHOICES),
        ("status", STATUS_CHOICES),
        ("scope", SCOPE_CHOICES),
        ("workspace", WORKSPACE_CHOICES),
        ("confidentiality", CONFIDENTIALITY_CHOICES),
        ("confidence", CONFIDENCE_CHOICES),
    ]:
        if field in record and record[field] not in choices:
            errors.append(f"{field} has invalid value")

    for field in ("title", "source"):
        if field in record and (not isinstance(record[field], str) or not record[field]):
            errors.append(f"{field} must be a non-empty string")
    if "content" in record and not isinstance(record["content"], str):
        errors.append("content must be a string")

    dates = {}
    for field in ("created", "updated", "valid_from", "valid_until", "effective_date"):
        if field in record:
            parsed = _real_date(record[field])
            if parsed is None:
                errors.append(f"{field} must be a real YYYY-MM-DD date")
            else:
                dates[field] = parsed
    if "created" in dates and "updated" in dates and dates["updated"] < dates["created"]:
        errors.append("updated cannot be earlier than created")
    if "valid_from" in dates and "valid_until" in dates and dates["valid_until"] < dates["valid_from"]:
        errors.append("valid_until cannot be earlier than valid_from")

    for field in LIST_FIELDS:
        if field in record:
            value = record[field]
            if not isinstance(value, list):
                errors.append(f"{field} must be a list")
            else:
                seen = set()
                for item in value:
                    if not isinstance(item, str) or not item:
                        errors.append(f"{field} items must be non-empty strings")
                    elif item in seen:
                        errors.append(f"{field} contains duplicate item: {item}")
                    seen.add(item)

    memory_type = record.get("type")
    scope = record.get("scope")
    if memory_type != expected_type:
        errors.append("type does not match directory")
    if memory_type == "context":
        if scope != "context":
            errors.append("type context requires scope context")
        if "context_id" not in record:
            errors.append("missing required field: context_id")
    if memory_type == "context_transition":
        if scope != "context":
            errors.append("type context_transition requires scope context")
        for field in ("from_context", "to_context", "effective_date", "reason"):
            if field not in record:
                errors.append(f"missing required field: {field}")
    if memory_type == "project":
        if scope != "project":
            errors.append("type project requires scope project")
        if "project" not in record:
            errors.append("missing required field: project")
    if scope == "context" and memory_type != "context_transition" and "context_id" not in record:
        errors.append("missing required field: context_id")
    if scope == "project" and "project" not in record:
        errors.append("missing required field: project")
    if record.get("confidentiality") in {"internal", "restricted"} and record.get("workspace") != "work":
        errors.append("confidentiality internal or restricted requires workspace work")

    return [(rel_path, message) for message in errors]


def _memory_paths(root):
    paths = []
    for relative_dir in TYPE_DIRS.values():
        directory = root / relative_dir
        if not directory.exists():
            continue
        for path in directory.rglob("*.md"):
            if path.is_dir():
                continue
            paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def collect_validated_records(root):
    root = _require_data_root(root)
    errors = []
    records = []
    paths = _memory_paths(root)
    for path in paths:
        rel_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append((rel_path, "memory file must not be a symbolic link"))
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            errors.append((rel_path, "resolved path is outside data root"))
            continue

        expected_type = None
        for relative_dir, memory_type in DIR_TYPES.items():
            if rel_path == relative_dir or rel_path.startswith(relative_dir + "/"):
                expected_type = memory_type
                break
        record, parse_errors = parse_front_matter(path)
        for message in parse_errors:
            errors.append((rel_path, message))
        if record is None:
            continue
        errors.extend(validate_record(record, path, rel_path, expected_type))
        records.append({"relative_path": rel_path, "record": record, "path": path})

    errors.extend(_validate_cross_file([(item["relative_path"], item["record"]) for item in records]))
    return root, records, errors, len(paths)


def validate_store(root):
    _, _, errors, count = collect_validated_records(root)
    return count, errors


def _validate_cross_file(records):
    errors = []
    ids = {}
    context_ids = set()
    active_contexts = {}
    for rel_path, record in records:
        memory_id = record.get("id")
        if isinstance(memory_id, str):
            ids.setdefault(memory_id, []).append(rel_path)
        if record.get("type") == "context" and isinstance(record.get("context_id"), str):
            context_ids.add(record["context_id"])
            if record.get("status") == "active" and isinstance(record.get("workspace"), str):
                active_contexts.setdefault(record["workspace"], []).append(rel_path)

    for memory_id in sorted(ids):
        paths = ids[memory_id]
        if len(paths) > 1:
            for rel_path in sorted(paths):
                errors.append((rel_path, f"duplicate id: {memory_id}"))

    for rel_path, record in records:
        if "context_id" in record and record.get("type") != "context" and record["context_id"] not in context_ids:
            errors.append((rel_path, "context_id does not reference an existing context"))
        if record.get("type") == "context_transition":
            from_context = record.get("from_context")
            to_context = record.get("to_context")
            if from_context not in context_ids:
                errors.append((rel_path, "from_context does not reference an existing context"))
            if to_context not in context_ids:
                errors.append((rel_path, "to_context does not reference an existing context"))
            if from_context and to_context and from_context == to_context:
                errors.append((rel_path, "from_context and to_context must differ"))

        memory_id = record.get("id")
        for field in ("supersedes", "superseded_by"):
            for value in record.get(field, []) if isinstance(record.get(field), list) else []:
                if value == memory_id:
                    errors.append((rel_path, f"{field} cannot reference itself"))
                elif value not in ids:
                    errors.append((rel_path, f"{field} references unknown id: {value}"))

    for workspace in sorted(active_contexts):
        paths = active_contexts[workspace]
        if len(paths) > 1:
            for rel_path in sorted(paths):
                errors.append((rel_path, f"multiple active contexts in workspace: {workspace}"))

    return errors


def atomic_write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{path.name}-{uuid.uuid4().hex}"
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def export_store(root, include_internal=False):
    root, records, errors, _ = collect_validated_records(root)
    if errors:
        return None, errors

    exported = []
    skipped_internal = 0
    skipped_restricted = 0
    for item in sorted(records, key=lambda value: value["relative_path"]):
        confidentiality = item["record"].get("confidentiality")
        if confidentiality == "restricted":
            skipped_restricted += 1
            continue
        if confidentiality == "internal" and not include_internal:
            skipped_internal += 1
            continue
        sha256 = hashlib.sha256(item["path"].read_bytes()).hexdigest()
        exported.append(
            {
                "record": item["record"],
                "relative_path": item["relative_path"],
                "sha256": sha256,
            }
        )

    jsonl = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for row in exported
    )
    manifest = {
        "format_version": 1,
        "records": [
            {
                "id": row["record"]["id"],
                "type": row["record"]["type"],
                "status": row["record"]["status"],
                "workspace": row["record"]["workspace"],
                "confidentiality": row["record"]["confidentiality"],
                "relative_path": row["relative_path"],
                "sha256": row["sha256"],
            }
            for row in exported
        ],
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"

    exports = root / "exports"
    atomic_write_text(exports / "memory.jsonl", jsonl)
    atomic_write_text(exports / "index_manifest.json", manifest_text)
    return {
        "exported": len(exported),
        "skipped_internal": skipped_internal,
        "skipped_restricted": skipped_restricted,
    }, []


def _transition_abort(messages):
    raise ValueError("\n".join(messages + ["Transition aborted because validation failed."]))


def _new_memory_target(root, memory_type, title, used_ids):
    target_dir = root / TYPE_DIRS[memory_type]
    today = date.today().isoformat().replace("-", "")
    slug = safe_slug(title)
    while True:
        memory_id = f"{memory_type}-{today}-{uuid.uuid4().hex[:8]}"
        target = target_dir / f"{memory_id}-{slug}.md"
        if memory_id not in used_ids and not target.exists():
            used_ids.add(memory_id)
            return memory_id, target


def _expected_type_for_path(root, path):
    rel_path = path.relative_to(root).as_posix()
    for relative_dir, memory_type in DIR_TYPES.items():
        if rel_path == relative_dir or rel_path.startswith(relative_dir + "/"):
            return memory_type
    return None


def _validate_hypothetical_records(root, records):
    errors = []
    pairs = []
    for item in records:
        rel_path = item["path"].relative_to(root).as_posix()
        errors.extend(validate_record(item["record"], item["path"], rel_path, _expected_type_for_path(root, item["path"])))
        pairs.append((rel_path, item["record"]))
    errors.extend(_validate_cross_file(pairs))
    return errors


def _prepare_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{path.name}-{uuid.uuid4().hex}"
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _replace_transaction(operations, cleanup_paths):
    prepared = []
    try:
        for path, content in operations:
            prepared.append((_prepare_text(path, content), path))
        for tmp, path in prepared:
            tmp.replace(path)
    except OSError:
        for tmp, _ in prepared:
            tmp.unlink(missing_ok=True)
        for path in cleanup_paths:
            path.unlink(missing_ok=True)
        raise


def context_transition(args):
    root, records, errors, _ = collect_validated_records(args.root)
    if errors:
        _transition_abort([f"ERROR {rel_path}: {message}" for rel_path, message in errors])

    messages = []
    if args.from_context == args.to_context:
        messages.append("ERROR context-transition: to-context must differ from from-context")
    if not (3 <= len(args.to_context) <= 128) or not ID_PATTERN.fullmatch(args.to_context):
        messages.append("ERROR context-transition: to-context must match memory id pattern")
    if args.confidentiality in {"internal", "restricted"} and args.workspace != "work":
        messages.append("ERROR context-transition: confidentiality internal or restricted requires workspace work")
    effective = _real_date(args.effective_date)
    if effective is None:
        messages.append("ERROR context-transition: effective-date must be a real YYYY-MM-DD date")

    contexts = [item for item in records if item["record"].get("type") == "context"]
    sources = [item for item in contexts if item["record"].get("context_id") == args.from_context]
    targets = [item for item in contexts if item["record"].get("context_id") == args.to_context]
    if len(sources) != 1:
        messages.append("ERROR context-transition: source context must exist exactly once")
    if targets:
        messages.append("ERROR context-transition: target context_id already exists")

    source_item = sources[0] if len(sources) == 1 else None
    if source_item is not None:
        source_record = source_item["record"]
        if source_record.get("status") != "active":
            messages.append("ERROR context-transition: source context must be active")
        source_valid_from = _real_date(source_record.get("valid_from"))
        if source_valid_from is None:
            messages.append("ERROR context-transition: source context must have valid valid_from")
        elif effective is not None and effective <= source_valid_from:
            messages.append("ERROR context-transition: effective-date must be later than source valid_from")
        for item in contexts:
            record = item["record"]
            if item is source_item:
                continue
            if record.get("status") == "active" and record.get("workspace") == args.workspace:
                messages.append("ERROR context-transition: target workspace already has an active context")
                break

    if messages:
        _transition_abort(messages)

    today = date.today().isoformat()
    used_ids = {
        item["record"].get("id")
        for item in records
        if isinstance(item["record"].get("id"), str)
    }
    source_record = dict(source_item["record"])
    source_record["status"] = "historical"
    source_record["updated"] = today
    source_record["valid_until"] = (effective - timedelta(days=1)).isoformat()

    new_context_id, new_context_path = _new_memory_target(root, "context", args.to_title, used_ids)
    transition_title = f"从「{source_record['title']}」迁移到「{args.to_title}」"
    transition_id, transition_path = _new_memory_target(root, "context_transition", transition_title, used_ids)
    superseded_by = list(source_record.get("superseded_by", []))
    if new_context_id not in superseded_by:
        superseded_by.append(new_context_id)
    source_record["superseded_by"] = superseded_by

    content = args.content if args.content is not None else args.reason
    new_context_record = {
        "id": new_context_id,
        "type": "context",
        "title": args.to_title,
        "created": today,
        "updated": today,
        "status": "active",
        "scope": "context",
        "workspace": args.workspace,
        "confidentiality": args.confidentiality,
        "source": args.source,
        "confidence": args.confidence,
        "context_id": args.to_context,
        "valid_from": args.effective_date,
        "supersedes": [source_record["id"]],
        "tags": args.tags or [],
        "content": content,
    }
    transition_record = {
        "id": transition_id,
        "type": "context_transition",
        "title": transition_title,
        "created": today,
        "updated": today,
        "status": "active",
        "scope": "context",
        "workspace": args.workspace,
        "confidentiality": args.confidentiality,
        "source": args.source,
        "confidence": args.confidence,
        "from_context": args.from_context,
        "to_context": args.to_context,
        "effective_date": args.effective_date,
        "reason": args.reason,
        "tags": args.tags or [],
        "content": content,
    }

    final_records = []
    for item in records:
        if item is source_item:
            final_records.append({**item, "record": source_record})
        else:
            final_records.append(item)
    final_records.extend(
        [
            {"relative_path": new_context_path.relative_to(root).as_posix(), "record": new_context_record, "path": new_context_path},
            {"relative_path": transition_path.relative_to(root).as_posix(), "record": transition_record, "path": transition_path},
        ]
    )
    final_errors = _validate_hypothetical_records(root, final_records)
    if final_errors:
        _transition_abort([f"ERROR {rel_path}: {message}" for rel_path, message in final_errors])

    source_path = source_item["path"]
    summary = {
        "from_context": args.from_context,
        "to_context": args.to_context,
        "effective_date": args.effective_date,
        "source_path": source_path.relative_to(root).as_posix(),
        "new_context_path": new_context_path.relative_to(root).as_posix(),
        "transition_path": transition_path.relative_to(root).as_posix(),
    }
    if args.dry_run:
        summary["dry_run"] = True
        return summary

    _replace_transaction(
        [
            (new_context_path, render_memory(new_context_record)),
            (transition_path, render_memory(transition_record)),
            (source_path, render_existing_memory(source_path, source_record)),
        ],
        [new_context_path, transition_path],
    )
    summary["dry_run"] = False
    return summary


def build_parser():
    parser = argparse.ArgumentParser(description="Manage file-based research memory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="Initialize a data root.")
    init_parser.add_argument("--root", required=True, help="Data root to initialize.")

    add_parser = subparsers.add_parser("add", help="Add a memory record.")
    add_parser.add_argument("--root", required=True, help="Initialized data root.")
    add_parser.add_argument("--type", required=True, choices=TYPE_CHOICES)
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--scope", required=True, choices=SCOPE_CHOICES)
    add_parser.add_argument("--workspace", required=True, choices=WORKSPACE_CHOICES)
    add_parser.add_argument("--confidentiality", required=True, choices=CONFIDENTIALITY_CHOICES)
    add_parser.add_argument("--source", required=True)
    add_parser.add_argument("--confidence", required=True, choices=CONFIDENCE_CHOICES)
    add_parser.add_argument("--content", required=True)
    add_parser.add_argument("--status", default="active", choices=STATUS_CHOICES)
    add_parser.add_argument("--context-id", dest="context_id")
    add_parser.add_argument("--project")
    add_parser.add_argument("--valid-from", dest="valid_from")
    add_parser.add_argument("--valid-until", dest="valid_until")
    add_parser.add_argument("--tags", nargs="*")
    add_parser.add_argument("--from-context", dest="from_context")
    add_parser.add_argument("--to-context", dest="to_context")
    add_parser.add_argument("--effective-date", dest="effective_date")
    add_parser.add_argument("--reason")

    validate_parser = subparsers.add_parser("validate", help="Validate memory files.")
    validate_parser.add_argument("--root", required=True, help="Initialized data root.")

    export_parser = subparsers.add_parser("export", help="Export memory records.")
    export_parser.add_argument("--root", required=True, help="Initialized data root.")
    export_parser.add_argument("--include-internal", action="store_true")

    transition_parser = subparsers.add_parser("context-transition", help="Migrate an active context.")
    transition_parser.add_argument("--root", required=True, help="Initialized data root.")
    transition_parser.add_argument("--from-context", required=True, dest="from_context")
    transition_parser.add_argument("--to-context", required=True, dest="to_context")
    transition_parser.add_argument("--to-title", required=True, dest="to_title")
    transition_parser.add_argument("--workspace", required=True, choices=WORKSPACE_CHOICES)
    transition_parser.add_argument("--confidentiality", required=True, choices=CONFIDENTIALITY_CHOICES)
    transition_parser.add_argument("--effective-date", required=True, dest="effective_date")
    transition_parser.add_argument("--reason", required=True)
    transition_parser.add_argument("--source", default="user")
    transition_parser.add_argument("--confidence", default="confirmed", choices=CONFIDENCE_CHOICES)
    transition_parser.add_argument("--content")
    transition_parser.add_argument("--tags", nargs="*")
    transition_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            root, created_dirs, created_files, existing_items = init_store(args.root)
            print(f"数据根目录: {root}")
            print(f"新建目录数量: {created_dirs}")
            print(f"新建文件数量: {created_files}")
            print(f"已存在项目数量: {existing_items}")
            return 0
        if args.command == "add":
            memory_id, relative_path, memory_type, status = add_memory(args)
            print(f"memory_id: {memory_id}")
            print(f"path: {relative_path}")
            print(f"type: {memory_type}")
            print(f"status: {status}")
            return 0
        if args.command == "validate":
            count, errors = validate_store(args.root)
            for rel_path, message in errors:
                print(f"ERROR {rel_path}: {message}")
            print(f"Validated: {count} files")
            print(f"Errors: {len(errors)}")
            print("Warnings: 0")
            return 1 if errors else 0
        if args.command == "export":
            summary, errors = export_store(args.root, args.include_internal)
            if errors:
                for rel_path, message in errors:
                    print(f"ERROR {rel_path}: {message}")
                print("Export aborted because validation failed.")
                return 1
            print(f"Exported: {summary['exported']} records")
            print(f"Skipped internal: {summary['skipped_internal']}")
            print(f"Skipped restricted: {summary['skipped_restricted']}")
            print("Output: exports/memory.jsonl")
            print("Manifest: exports/index_manifest.json")
            return 0
        if args.command == "context-transition":
            summary = context_transition(args)
            if summary["dry_run"]:
                print("Dry run: no files changed")
                print(f"From context: {summary['from_context']}")
                print(f"To context: {summary['to_context']}")
                print(f"Effective date: {summary['effective_date']}")
                print(f"Source file: {summary['source_path']}")
                print(f"New context: {summary['new_context_path']}")
                print(f"Transition: {summary['transition_path']}")
                return 0
            print("Transition completed")
            print(f"From context: {summary['from_context']}")
            print(f"To context: {summary['to_context']}")
            print(f"Effective date: {summary['effective_date']}")
            print(f"Updated: {summary['source_path']}")
            print(f"Created context: {summary['new_context_path']}")
            print(f"Created transition: {summary['transition_path']}")
            return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
