import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
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
    "imports/chatgpt/conversations",
    "imports/manual/raw",
    "imports/manual/text",
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
DB_NAME = "memory.sqlite"
DB_SCHEMA_VERSION = 3
STATE_DIR_ERROR = "SQLite state directory must be local and outside iCloud and the code repository."
MEMORIES_COLUMNS = [
    "id",
    "type",
    "title",
    "content",
    "tags",
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
    "created",
    "updated",
    "relative_path",
    "sha256",
]
INDEX_STATE_COLUMNS = ["relative_path", "sha256", "mtime_ns", "indexed_at"]
DOCUMENTS_COLUMNS = [
    "id",
    "source_kind",
    "source_id",
    "title",
    "content",
    "workspace",
    "confidentiality",
    "project",
    "context_id",
    "updated",
    "relative_path",
    "sha256",
    "metadata_json",
]
DOCUMENT_INDEX_STATE_COLUMNS = ["relative_path", "sha256", "mtime_ns", "indexed_at"]
REQUIRED_INDEXES = {
    "idx_memories_type",
    "idx_memories_project",
    "idx_memories_context",
    "idx_memories_workspace_status",
}
REQUIRED_DOCUMENT_INDEXES = {
    "idx_documents_source_kind",
    "idx_documents_project",
    "idx_documents_workspace_confidentiality",
}
CJK_RUN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]+")


def _repository_root():
    for path in Path(__file__).resolve().parents:
        if (path / ".git").exists():
            return path
    return None


def _icloud_root():
    return Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"


def default_state_dir():
    return Path.home() / "Library" / "Application Support" / "ResearchAgent"


def _contains_or_equals(parent, child):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


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


def check_fts5():
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(content)")
    except sqlite3.DatabaseError as exc:
        raise ValueError("SQLite FTS5 is not available in this Python build.") from exc


def resolve_state_dir(value):
    return Path(value).expanduser().resolve(strict=False)


def check_state_dir(root, state_dir):
    raw_state = Path(state_dir).expanduser()
    if raw_state.exists() and (raw_state.is_symlink() or not raw_state.is_dir()):
        raise ValueError(STATE_DIR_ERROR)

    root = Path(root).expanduser().resolve(strict=True)
    state = raw_state.resolve(strict=False)
    repo_root = _repository_root()
    icloud = _icloud_root().expanduser().resolve(strict=False)

    if (
        state == root
        or _contains_or_equals(root, state)
        or _contains_or_equals(state, root)
        or (repo_root is not None and _contains_or_equals(repo_root.resolve(), state))
        or _contains_or_equals(icloud, state)
    ):
        raise ValueError(STATE_DIR_ERROR)

    db = state / DB_NAME
    if db.is_symlink():
        raise ValueError(STATE_DIR_ERROR)
    return state, db


def _configure_sqlite(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        raise ValueError("SQLite journal_mode WAL could not be enabled.")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def _create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            scope TEXT NOT NULL,
            workspace TEXT NOT NULL,
            confidentiality TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence TEXT NOT NULL,
            context_id TEXT,
            project TEXT,
            valid_from TEXT,
            valid_until TEXT,
            created TEXT NOT NULL,
            updated TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL
        );
        CREATE INDEX idx_memories_type ON memories(type);
        CREATE INDEX idx_memories_project ON memories(project);
        CREATE INDEX idx_memories_context ON memories(context_id);
        CREATE INDEX idx_memories_workspace_status
            ON memories(workspace, status);
        CREATE VIRTUAL TABLE memory_fts USING fts5(
            id UNINDEXED,
            title,
            content,
            tags,
            tokenize = 'unicode61'
        );
        CREATE TABLE index_state (
            relative_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_id TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            workspace TEXT NOT NULL,
            confidentiality TEXT NOT NULL,
            project TEXT,
            context_id TEXT,
            updated TEXT,
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX idx_documents_source_kind ON documents(source_kind);
        CREATE INDEX idx_documents_project ON documents(project);
        CREATE INDEX idx_documents_workspace_confidentiality
            ON documents(workspace, confidentiality);
        CREATE VIRTUAL TABLE document_fts USING fts5(
            id UNINDEXED,
            title,
            content,
            source_kind,
            project,
            tokenize = 'unicode61'
        );
        CREATE TABLE document_index_state (
            relative_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
        PRAGMA user_version = 3;
        """
    )


def _table_columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _target_tables(conn):
    return {
        name: (object_type, sql or "")
        for name, object_type, sql in conn.execute(
            """
            SELECT name, type, sql
            FROM sqlite_master
            WHERE name IN (
                'memories', 'memory_fts', 'index_state',
                'documents', 'document_fts', 'document_index_state'
            )
            """
        )
    }


def inspect_database(db):
    try:
        with sqlite3.connect(db) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            tables = _target_tables(conn)
            if version != DB_SCHEMA_VERSION:
                if version == 0 and not tables:
                    return {"initialized": False, "version": version}
                raise ValueError("Incompatible SQLite schema version.")
            if str(mode).lower() != "wal":
                raise ValueError("SQLite journal_mode WAL could not be verified.")
            required_tables = {
                "memories",
                "memory_fts",
                "index_state",
                "documents",
                "document_fts",
                "document_index_state",
            }
            if set(tables) != required_tables:
                raise ValueError("SQLite schema is incomplete.")
            if "CREATE VIRTUAL TABLE" not in tables["memory_fts"][1].upper():
                raise ValueError("SQLite FTS5 table is missing.")
            if "CREATE VIRTUAL TABLE" not in tables["document_fts"][1].upper():
                raise ValueError("SQLite FTS5 table is missing.")
            if _table_columns(conn, "memories") != MEMORIES_COLUMNS:
                raise ValueError("SQLite memories table columns do not match.")
            if _table_columns(conn, "index_state") != INDEX_STATE_COLUMNS:
                raise ValueError("SQLite index_state table columns do not match.")
            if _table_columns(conn, "documents") != DOCUMENTS_COLUMNS:
                raise ValueError("SQLite documents table columns do not match.")
            if _table_columns(conn, "document_index_state") != DOCUMENT_INDEX_STATE_COLUMNS:
                raise ValueError("SQLite document_index_state table columns do not match.")
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_memories_%'"
                )
            }
            if not REQUIRED_INDEXES.issubset(indexes):
                raise ValueError("SQLite memories indexes are incomplete.")
            document_indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_documents_%'"
                )
            }
            if not REQUIRED_DOCUMENT_INDEXES.issubset(document_indexes):
                raise ValueError("SQLite documents indexes are incomplete.")
            return {"initialized": True, "version": version}
    except sqlite3.DatabaseError as exc:
        raise ValueError("Invalid SQLite database.") from exc


def _checkpoint_and_close(conn):
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _remove_sqlite_sidecars(path):
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def checkpoint_database(db):
    try:
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as exc:
        raise ValueError("Invalid SQLite database.") from exc
    _remove_sqlite_sidecars(db)


def create_database(db):
    tmp = db.parent / f".tmp-{DB_NAME}-{uuid.uuid4().hex}"
    conn = None
    try:
        conn = sqlite3.connect(tmp)
        _configure_sqlite(conn)
        _create_schema(conn)
        _checkpoint_and_close(conn)
        conn = None
        inspect_database(tmp)
        tmp.replace(db)
        _remove_sqlite_sidecars(tmp)
        inspect_database(db)
        _remove_sqlite_sidecars(db)
    except Exception:
        if conn is not None:
            conn.close()
        tmp.unlink(missing_ok=True)
        _remove_sqlite_sidecars(tmp)
        raise


def initialize_empty_database(db):
    try:
        conn = sqlite3.connect(db)
        _configure_sqlite(conn)
        _create_schema(conn)
        _checkpoint_and_close(conn)
        inspect_database(db)
    except sqlite3.DatabaseError as exc:
        raise ValueError("Invalid SQLite database.") from exc


def db_init(args):
    root, _, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Database initialization aborted because validation failed."]))

    check_fts5()
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    state, db = check_state_dir(root, state_dir)
    already = False

    if db.exists():
        summary = inspect_database(db)
        if summary["initialized"]:
            checkpoint_database(db)
            already = True
        else:
            initialize_empty_database(db)
    else:
        state.mkdir(parents=True, exist_ok=True)
        create_database(db)

    return {
        "already": already,
        "state_dir": state,
        "database": db,
        "version": DB_SCHEMA_VERSION,
        "tables": ["memories", "memory_fts", "index_state", "documents", "document_fts", "document_index_state"],
    }


def db_rebuild(args):
    root, _, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Database rebuild aborted because validation failed."]))

    check_fts5()
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    state, db = check_state_dir(root, state_dir)
    state.mkdir(parents=True, exist_ok=True)
    tmp_state = state / f".rebuild-{uuid.uuid4().hex}"
    tmp_db = tmp_state / DB_NAME
    try:
        tmp_state.mkdir()
        create_database(tmp_db)
        index_summary = index_store(argparse.Namespace(root=str(root), state_dir=str(tmp_state), dry_run=False))
        _remove_sqlite_sidecars(db)
        tmp_db.replace(db)
        _remove_sqlite_sidecars(tmp_db)
        checkpoint_database(db)
        return {
            "database": db,
            "version": DB_SCHEMA_VERSION,
            "memories": index_summary["memories"],
            "documents": index_summary["documents"],
        }
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)


def normalize_fts_text(value):
    text = "" if value is None else str(value)
    parts = []
    last = 0
    for match in CJK_RUN.finditer(text):
        if match.start() > last:
            parts.append(text[last:match.start()])
        run = match.group(0)
        grams = [run]
        if len(run) >= 2:
            grams.extend(run[index : index + 2] for index in range(len(run) - 1))
        parts.append(" ".join(grams))
        last = match.end()
    if last < len(text):
        parts.append(text[last:])
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _record_tags(record):
    tags = record.get("tags", [])
    return tags if isinstance(tags, list) else []


def _record_to_db_values(item):
    record = item["record"]
    return {
        "id": record["id"],
        "type": record["type"],
        "title": record["title"],
        "content": record["content"],
        "tags": json.dumps(_record_tags(record), ensure_ascii=False, separators=(",", ":")),
        "status": record["status"],
        "scope": record["scope"],
        "workspace": record["workspace"],
        "confidentiality": record["confidentiality"],
        "source": record["source"],
        "confidence": record["confidence"],
        "context_id": record.get("context_id"),
        "project": record.get("project"),
        "valid_from": record.get("valid_from"),
        "valid_until": record.get("valid_until"),
        "created": record["created"],
        "updated": record["updated"],
        "relative_path": item["relative_path"],
        "sha256": item["sha256"],
    }


def _fts_values(item):
    record = item["record"]
    return (
        record["id"],
        normalize_fts_text(record["title"]),
        normalize_fts_text(record["content"]),
        normalize_fts_text(" ".join(_record_tags(record))),
    )


def _current_index_items(root, records):
    items = []
    for item in records:
        path = item["path"]
        raw = path.read_bytes()
        items.append(
            {
                "relative_path": item["relative_path"],
                "record": item["record"],
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mtime_ns": path.stat().st_mtime_ns,
            }
        )
    return sorted(items, key=lambda value: value["relative_path"])


def document_source_kind(relative_path):
    if relative_path.startswith("imports/chatgpt/conversations/"):
        return "chatgpt"
    if relative_path.startswith("imports/manual/raw/"):
        return "manual"
    if relative_path.startswith("imports/manual/text/"):
        return "manual"
    if relative_path.startswith("literature/notes/"):
        return "literature"
    if relative_path.startswith("literature/journals/"):
        return "journal"
    if (
        relative_path.startswith("manuscripts/current/")
        or relative_path.startswith("manuscripts/evidence/")
        or relative_path.startswith("manuscripts/archive/")
    ):
        return "manuscript"
    return None


def parse_document_frontmatter(text):
    metadata = {
        "workspace": "personal",
        "confidentiality": "personal",
        "project": None,
        "context_id": None,
    }
    body = text
    if text.startswith("---\n"):
        lines = text.splitlines()
        try:
            end = lines.index("---", 1)
        except ValueError:
            return metadata, text
        for line in lines[1:end]:
            if ":" not in line or line.startswith(" "):
                continue
            key, raw = line.split(":", 1)
            key = key.strip()
            raw = raw.strip()
            if key not in {"title", "conversation_id", "archive_id", "workspace", "confidentiality", "project", "context_id", "updated"}:
                continue
            try:
                value = json.loads(raw) if raw.startswith('"') else raw
            except json.JSONDecodeError:
                value = raw
            if value == "null":
                value = None
            metadata[key] = value
        body = "\n".join(lines[end + 1 :]).strip()
    return metadata, body


def _sha16(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def document_id_for_path(relative_path, source_kind, source_id, sha256):
    if source_kind == "chatgpt" and source_id:
        return f"doc:chatgpt:{source_id}"
    if source_kind == "manual":
        return f"doc:manual:{sha256[:16]}"
    return f"doc:file:{_sha16(relative_path)}"


def _document_title(path, metadata):
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return path.stem or path.name


def collect_document_items(root):
    root = _require_data_root(root)
    items = []
    for base in [
        root / "imports" / "chatgpt" / "conversations",
        root / "imports" / "manual" / "raw",
        root / "imports" / "manual" / "text",
        root / "literature" / "notes",
        root / "literature" / "journals",
        root / "manuscripts" / "current",
        root / "manuscripts" / "evidence",
        root / "manuscripts" / "archive",
    ]:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel_path = path.relative_to(root).as_posix()
            source_kind = document_source_kind(rel_path)
            if source_kind is None:
                continue
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            metadata, body = parse_document_frontmatter(text)
            if not body.strip():
                continue
            source_id = metadata.get("conversation_id") or metadata.get("archive_id")
            sha256 = hashlib.sha256(raw).hexdigest()
            item = {
                "id": document_id_for_path(rel_path, source_kind, source_id, sha256),
                "source_kind": source_kind,
                "source_id": source_id,
                "title": _document_title(path, metadata),
                "content": body.strip(),
                "workspace": metadata.get("workspace") or "personal",
                "confidentiality": metadata.get("confidentiality") or "personal",
                "project": metadata.get("project"),
                "context_id": metadata.get("context_id"),
                "updated": metadata.get("updated"),
                "relative_path": rel_path,
                "sha256": sha256,
                "mtime_ns": path.stat().st_mtime_ns,
                "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
            items.append(item)
    return sorted(items, key=lambda value: value["relative_path"])


def _database_index_state(conn):
    memories = [
        dict(zip(MEMORIES_COLUMNS, row))
        for row in conn.execute("SELECT " + ", ".join(MEMORIES_COLUMNS) + " FROM memories")
    ]
    index_state = {
        row[0]: {"sha256": row[1], "mtime_ns": row[2], "indexed_at": row[3]}
        for row in conn.execute("SELECT relative_path, sha256, mtime_ns, indexed_at FROM index_state")
    }
    fts_counts = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, COUNT(*) FROM memory_fts GROUP BY id")
    }
    return {
        "memories_by_id": {row["id"]: row for row in memories},
        "memories_by_path": {row["relative_path"]: row for row in memories},
        "index_state": index_state,
        "fts_counts": fts_counts,
    }


def _database_document_state(conn):
    documents = [
        dict(zip(DOCUMENTS_COLUMNS, row))
        for row in conn.execute("SELECT " + ", ".join(DOCUMENTS_COLUMNS) + " FROM documents")
    ]
    index_state = {
        row[0]: {"sha256": row[1], "mtime_ns": row[2], "indexed_at": row[3]}
        for row in conn.execute("SELECT relative_path, sha256, mtime_ns, indexed_at FROM document_index_state")
    }
    fts_counts = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, COUNT(*) FROM document_fts GROUP BY id")
    }
    return {
        "documents_by_id": {row["id"]: row for row in documents},
        "documents_by_path": {row["relative_path"]: row for row in documents},
        "index_state": index_state,
        "fts_counts": fts_counts,
    }


def plan_index_changes(conn, items):
    state = _database_index_state(conn)
    current_paths = {item["relative_path"] for item in items}
    current_ids = {item["record"]["id"] for item in items}
    added = []
    updated = []
    unchanged = []
    deleted = []

    for relative_path in sorted(set(state["index_state"]) - current_paths):
        old = state["memories_by_path"].get(relative_path)
        if old and old["id"] in current_ids:
            continue
        deleted.append({"relative_path": relative_path, "id": old["id"] if old else None})

    for item in items:
        memory_id = item["record"]["id"]
        relative_path = item["relative_path"]
        state_row = state["index_state"].get(relative_path)
        memory_row = state["memories_by_id"].get(memory_id)
        path_row = state["memories_by_path"].get(relative_path)
        fts_count = state["fts_counts"].get(memory_id, 0)

        if (
            state_row
            and state_row["sha256"] == item["sha256"]
            and memory_row
            and memory_row["relative_path"] == relative_path
            and path_row
            and path_row["id"] == memory_id
            and fts_count == 1
        ):
            unchanged.append(item)
        elif not state_row and memory_id not in state["memories_by_id"] and not path_row:
            added.append(item)
        else:
            updated.append(item)

    return {
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
        "state": state,
    }


def plan_document_changes(conn, items):
    state = _database_document_state(conn)
    current_paths = {item["relative_path"] for item in items}
    current_ids = {item["id"] for item in items}
    added = []
    updated = []
    unchanged = []
    deleted = []

    for relative_path in sorted(set(state["index_state"]) - current_paths):
        old = state["documents_by_path"].get(relative_path)
        if old and old["id"] in current_ids:
            continue
        deleted.append({"relative_path": relative_path, "id": old["id"] if old else None})

    for item in items:
        document_id = item["id"]
        relative_path = item["relative_path"]
        state_row = state["index_state"].get(relative_path)
        document_row = state["documents_by_id"].get(document_id)
        path_row = state["documents_by_path"].get(relative_path)
        fts_count = state["fts_counts"].get(document_id, 0)
        if (
            state_row
            and state_row["sha256"] == item["sha256"]
            and document_row
            and document_row["relative_path"] == relative_path
            and path_row
            and path_row["id"] == document_id
            and fts_count == 1
        ):
            unchanged.append(item)
        elif not state_row and document_id not in state["documents_by_id"] and not path_row:
            added.append(item)
        else:
            updated.append(item)

    return {
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
        "state": state,
    }


def _upsert_memory(conn, item, indexed_at):
    values = _record_to_db_values(item)
    columns = MEMORIES_COLUMNS
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id")
    conn.execute(
        f"""
        INSERT INTO memories ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}
        """,
        tuple(values[column] for column in columns),
    )
    conn.execute(
        "INSERT INTO memory_fts(id, title, content, tags) VALUES (?, ?, ?, ?)",
        _fts_values(item),
    )
    conn.execute(
        """
        INSERT INTO index_state(relative_path, sha256, mtime_ns, indexed_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            sha256 = excluded.sha256,
            mtime_ns = excluded.mtime_ns,
            indexed_at = excluded.indexed_at
        """,
        (item["relative_path"], item["sha256"], item["mtime_ns"], indexed_at),
    )


def _document_fts_values(item):
    return (
        item["id"],
        normalize_fts_text(item["title"]),
        normalize_fts_text(item["content"]),
        normalize_fts_text(item["source_kind"]),
        normalize_fts_text(item.get("project") or ""),
    )


def _upsert_document(conn, item, indexed_at):
    columns = DOCUMENTS_COLUMNS
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id")
    conn.execute(
        f"""
        INSERT INTO documents ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}
        """,
        tuple(item[column] for column in columns),
    )
    conn.execute(
        "INSERT INTO document_fts(id, title, content, source_kind, project) VALUES (?, ?, ?, ?, ?)",
        _document_fts_values(item),
    )
    conn.execute(
        """
        INSERT INTO document_index_state(relative_path, sha256, mtime_ns, indexed_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(relative_path) DO UPDATE SET
            sha256 = excluded.sha256,
            mtime_ns = excluded.mtime_ns,
            indexed_at = excluded.indexed_at
        """,
        (item["relative_path"], item["sha256"], item["mtime_ns"], indexed_at),
    )


def check_index_consistency(conn, expected_count):
    counts = {
        "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "index_state": conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0],
        "fts_distinct": conn.execute("SELECT COUNT(DISTINCT id) FROM memory_fts").fetchone()[0],
    }
    if any(value != expected_count for value in counts.values()):
        raise ValueError("SQLite index consistency check failed.")
    if conn.execute("SELECT id FROM memory_fts GROUP BY id HAVING COUNT(*) > 1").fetchone():
        raise ValueError("SQLite index consistency check failed.")
    if conn.execute(
        """
        SELECT memories.relative_path
        FROM memories
        LEFT JOIN index_state USING(relative_path)
        WHERE index_state.relative_path IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite index consistency check failed.")
    if conn.execute(
        """
        SELECT index_state.relative_path
        FROM index_state
        LEFT JOIN memories USING(relative_path)
        WHERE memories.relative_path IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite index consistency check failed.")
    if conn.execute(
        """
        SELECT memories.id
        FROM memories
        LEFT JOIN memory_fts ON memory_fts.id = memories.id
        WHERE memory_fts.id IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite index consistency check failed.")


def check_document_index_consistency(conn, expected_count):
    counts = {
        "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "document_index_state": conn.execute("SELECT COUNT(*) FROM document_index_state").fetchone()[0],
        "fts_distinct": conn.execute("SELECT COUNT(DISTINCT id) FROM document_fts").fetchone()[0],
    }
    if any(value != expected_count for value in counts.values()):
        raise ValueError("SQLite document index consistency check failed.")
    if conn.execute("SELECT id FROM document_fts GROUP BY id HAVING COUNT(*) > 1").fetchone():
        raise ValueError("SQLite document index consistency check failed.")
    if conn.execute(
        """
        SELECT documents.relative_path
        FROM documents
        LEFT JOIN document_index_state USING(relative_path)
        WHERE document_index_state.relative_path IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite document index consistency check failed.")
    if conn.execute(
        """
        SELECT document_index_state.relative_path
        FROM document_index_state
        LEFT JOIN documents USING(relative_path)
        WHERE documents.relative_path IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite document index consistency check failed.")
    if conn.execute(
        """
        SELECT documents.id
        FROM documents
        LEFT JOIN document_fts ON document_fts.id = documents.id
        WHERE document_fts.id IS NULL
        """
    ).fetchone():
        raise ValueError("SQLite document index consistency check failed.")


def apply_index_changes(conn, plan, expected_count):
    indexed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for item in plan["deleted"]:
        if item["id"]:
            conn.execute("DELETE FROM memory_fts WHERE id = ?", (item["id"],))
            conn.execute("DELETE FROM memories WHERE id = ?", (item["id"],))
        conn.execute("DELETE FROM memories WHERE relative_path = ?", (item["relative_path"],))
        conn.execute("DELETE FROM index_state WHERE relative_path = ?", (item["relative_path"],))

    for item in plan["added"] + plan["updated"]:
        memory_id = item["record"]["id"]
        old = plan["state"]["memories_by_id"].get(memory_id)
        if old and old["relative_path"] != item["relative_path"]:
            conn.execute("DELETE FROM index_state WHERE relative_path = ?", (old["relative_path"],))
        conflicts = conn.execute(
            "SELECT id FROM memories WHERE relative_path = ? AND id != ?",
            (item["relative_path"], memory_id),
        ).fetchall()
        for (conflict_id,) in conflicts:
            conn.execute("DELETE FROM memory_fts WHERE id = ?", (conflict_id,))
            conn.execute("DELETE FROM memories WHERE id = ?", (conflict_id,))
        conn.execute("DELETE FROM memory_fts WHERE id = ?", (memory_id,))
        _upsert_memory(conn, item, indexed_at)

    check_index_consistency(conn, expected_count)


def apply_document_changes(conn, plan, expected_count):
    indexed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for item in plan["deleted"]:
        if item["id"]:
            conn.execute("DELETE FROM document_fts WHERE id = ?", (item["id"],))
            conn.execute("DELETE FROM documents WHERE id = ?", (item["id"],))
        conn.execute("DELETE FROM documents WHERE relative_path = ?", (item["relative_path"],))
        conn.execute("DELETE FROM document_index_state WHERE relative_path = ?", (item["relative_path"],))

    for item in plan["added"] + plan["updated"]:
        document_id = item["id"]
        old = plan["state"]["documents_by_id"].get(document_id)
        if old and old["relative_path"] != item["relative_path"]:
            conn.execute("DELETE FROM document_index_state WHERE relative_path = ?", (old["relative_path"],))
        conflicts = conn.execute(
            "SELECT id FROM documents WHERE relative_path = ? AND id != ?",
            (item["relative_path"], document_id),
        ).fetchall()
        for (conflict_id,) in conflicts:
            conn.execute("DELETE FROM document_fts WHERE id = ?", (conflict_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (conflict_id,))
        conn.execute("DELETE FROM document_fts WHERE id = ?", (document_id,))
        _upsert_document(conn, item, indexed_at)

    check_document_index_consistency(conn, expected_count)


def index_store(args):
    root, records, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Index aborted because validation failed."]))

    state_dir = args.state_dir if args.state_dir else default_state_dir()
    state, db = check_state_dir(root, state_dir)
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init first.")
    summary = inspect_database(db)
    if not summary["initialized"]:
        raise ValueError("Database is not initialized. Run db-init first.")

    items = _current_index_items(root, records)
    document_items = collect_document_items(root)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        plan = plan_index_changes(conn, items)
        document_plan = plan_document_changes(conn, document_items)
        result = {
            "added": len(plan["added"]),
            "updated": len(plan["updated"]),
            "deleted": len(plan["deleted"]),
            "unchanged": len(plan["unchanged"]),
            "memories": {
                "added": len(plan["added"]),
                "updated": len(plan["updated"]),
                "deleted": len(plan["deleted"]),
                "unchanged": len(plan["unchanged"]),
            },
            "documents": {
                "added": len(document_plan["added"]),
                "updated": len(document_plan["updated"]),
                "deleted": len(document_plan["deleted"]),
                "unchanged": len(document_plan["unchanged"]),
            },
            "database": db,
            "dry_run": args.dry_run,
        }
        if args.dry_run:
            return result
        try:
            conn.execute("BEGIN")
            apply_index_changes(conn, plan, len(items))
            apply_document_changes(conn, document_plan, len(document_items))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    checkpoint_database(db)
    return result


def _query_tokens(text):
    parts = []
    last = 0
    for match in CJK_RUN.finditer(text):
        parts.extend(text[last : match.start()].split())
        run = match.group(0)
        parts.extend(run[index : index + 2] for index in range(max(1, len(run) - 1)))
        last = match.end()
    parts.extend(text[last:].split())
    return [part for part in parts if part]


def _fts_query(text):
    tokens = _query_tokens(text)
    if not tokens:
        raise ValueError("Search query must not be empty.")
    return " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _index_fresh(conn, memory_items, document_items):
    memory_state = _database_index_state(conn)
    document_state = _database_document_state(conn)
    current_memory = {item["relative_path"]: item["sha256"] for item in memory_items}
    current_documents = {item["relative_path"]: item["sha256"] for item in document_items}
    indexed_memory = {key: value["sha256"] for key, value in memory_state["index_state"].items()}
    indexed_documents = {key: value["sha256"] for key, value in document_state["index_state"].items()}
    return current_memory == indexed_memory and current_documents == indexed_documents


def _search_memory(conn, args, query):
    conditions = ["memory_fts MATCH ?", "m.confidentiality IN ('public', 'personal')", "m.status = 'active'"]
    values = [query]
    if args.project:
        if args.include_unassigned:
            conditions.append("(m.project = ? OR m.project IS NULL)")
        else:
            conditions.append("m.project = ?")
        values.append(args.project)
    if args.workspace:
        conditions.append("m.workspace = ?")
        values.append(args.workspace)
    if args.status:
        conditions.append("m.status = ?")
        values.append(args.status)
    values.append(args.limit)
    sql = f"""
        SELECT m.id, m.title, m.type, m.status, m.workspace, m.confidentiality,
               m.project, m.context_id, m.updated, m.relative_path,
               snippet(memory_fts, 2, '[', ']', '…', 20) AS excerpt,
               bm25(memory_fts) AS rank
        FROM memory_fts
        JOIN memories AS m ON m.id = memory_fts.id
        WHERE {' AND '.join(conditions)}
        ORDER BY rank, m.updated DESC, m.id
        LIMIT ?
    """
    rows = []
    for index, row in enumerate(conn.execute(sql, values), start=1):
        data = dict(row)
        data.update({"kind": "memory", "source_kind": "memory", "score": 1.20 / (60 + index)})
        rows.append(data)
    return rows


def _search_documents(conn, args, query):
    conditions = ["document_fts MATCH ?", "d.confidentiality IN ('public', 'personal')"]
    values = [query]
    if args.source_kind:
        conditions.append("d.source_kind = ?")
        values.append(args.source_kind)
    if args.project:
        if args.include_unassigned:
            conditions.append("(d.project = ? OR d.project IS NULL)")
        else:
            conditions.append("d.project = ?")
        values.append(args.project)
    if args.workspace:
        conditions.append("d.workspace = ?")
        values.append(args.workspace)
    values.append(args.limit)
    sql = f"""
        SELECT d.id, d.source_kind, d.title, NULL AS type, NULL AS status,
               d.workspace, d.confidentiality, d.project, d.context_id,
               d.updated, d.relative_path,
               snippet(document_fts, 2, '[', ']', '…', 20) AS excerpt,
               bm25(document_fts) AS rank
        FROM document_fts
        JOIN documents AS d ON d.id = document_fts.id
        WHERE {' AND '.join(conditions)}
        ORDER BY rank, d.updated DESC, d.id
        LIMIT ?
    """
    rows = []
    for index, row in enumerate(conn.execute(sql, values), start=1):
        data = dict(row)
        data.update({"kind": "document", "score": 1.00 / (60 + index)})
        rows.append(data)
    return rows


def search_store(args):
    root = _require_data_root(args.root)
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    _, db = check_state_dir(root, state_dir)
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init and index first.")
    summary = inspect_database(db)
    if not summary["initialized"]:
        raise ValueError("Database is not initialized. Run db-init and index first.")

    query = _fts_query(args.query)
    root, records, errors, _ = collect_validated_records(root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Search aborted because validation failed."]))
    memory_items = _current_index_items(root, records)
    document_items = collect_document_items(root)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if not _index_fresh(conn, memory_items, document_items):
            raise ValueError("Search aborted because the index is stale. Run index first.")
        rows = []
        if args.kind in {"all", "memory"} and not args.source_kind:
            rows.extend(_search_memory(conn, args, query))
        if args.kind in {"all", "document"}:
            rows.extend(_search_documents(conn, args, query))
    rows.sort(key=lambda row: (-row["score"], row["kind"], row["id"]))
    return rows[: args.limit]


def _state_sha_map(state):
    return {key: value["sha256"] for key, value in state["index_state"].items()}


def _freshness_details(conn, memory_items, document_items):
    memory_state = _database_index_state(conn)
    document_state = _database_document_state(conn)
    current_memory = {item["relative_path"]: item["sha256"] for item in memory_items}
    current_documents = {item["relative_path"]: item["sha256"] for item in document_items}
    indexed_memory = _state_sha_map(memory_state)
    indexed_documents = _state_sha_map(document_state)
    return {
        "memory_index_fresh": current_memory == indexed_memory,
        "document_index_fresh": current_documents == indexed_documents,
        "hash_mismatches": [
            {"kind": kind, "relative_path": path}
            for kind, current, indexed in [
                ("memory", current_memory, indexed_memory),
                ("document", current_documents, indexed_documents),
            ]
            for path in sorted(set(current) & set(indexed))
            if current[path] != indexed[path]
        ],
    }


def _relative_stems(root, relative_dir):
    base = root / relative_dir
    if not base.exists():
        return set()
    return {
        path.relative_to(base).with_suffix("").as_posix()
        for path in base.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _old_network_residuals():
    repo = Path(__file__).resolve().parents[1]
    files = [
        repo / "src" / "memory_tools.py",
        repo / "README.md",
    ]
    docs = repo / "docs"
    if docs.exists():
        files.extend(path for path in docs.rglob("*") if path.is_file())
    patterns = [
        "serve-" + "chatgpt",
        "Bear" + "er",
        "Threading" + "HTTPServer",
        "BaseHTTP" + "RequestHandler",
        "G" + "mail",
        "approve-" + "host",
        "Launch" + "Agent",
        "Tail" + "scale " + "Fun" + "nel",
    ]
    residuals = []
    for path in sorted(files):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in patterns:
            if pattern in text:
                residuals.append({"path": path.relative_to(repo).as_posix(), "pattern": pattern})
    return residuals


def doctor_store(args):
    root = _require_data_root(args.root)
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    result = {
        "data_root": str(root),
        "data_root_ok": True,
        "schema_version": None,
        "sqlite_initialized": False,
        "wal": False,
        "memory_index_fresh": None,
        "document_index_fresh": None,
        "hash_mismatches": [],
        "manual_orphan_raw": [],
        "manual_orphan_text": [],
        "old_network_residuals": [],
        "errors": [],
    }
    result["old_network_residuals"] = _old_network_residuals()
    raw_stems = _relative_stems(root, "imports/manual/raw")
    text_stems = _relative_stems(root, "imports/manual/text")
    result["manual_orphan_raw"] = sorted(raw_stems - text_stems)
    result["manual_orphan_text"] = sorted(text_stems - raw_stems)

    try:
        _, db = check_state_dir(root, state_dir)
        if not db.exists():
            result["errors"].append("database missing")
            return result
        summary = inspect_database(db)
        result["schema_version"] = summary["version"]
        result["sqlite_initialized"] = summary["initialized"]
        if not summary["initialized"]:
            result["errors"].append("database not initialized")
            return result
        records_root, records, errors, _ = collect_validated_records(root)
        if errors:
            result["errors"].extend(f"{rel_path}: {message}" for rel_path, message in errors)
            return result
        memory_items = _current_index_items(records_root, records)
        document_items = collect_document_items(root)
        with sqlite3.connect(db) as conn:
            result["wal"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
            result.update(_freshness_details(conn, memory_items, document_items))
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        result["errors"].append(str(exc))
    return result


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

    db_init_parser = subparsers.add_parser("db-init", help="Initialize the local SQLite FTS5 database.")
    db_init_parser.add_argument("--root", required=True, help="Initialized data root.")
    db_init_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )

    db_rebuild_parser = subparsers.add_parser("db-rebuild", help="Rebuild the local SQLite index from files.")
    db_rebuild_parser.add_argument("--root", required=True, help="Initialized data root.")
    db_rebuild_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )

    index_parser = subparsers.add_parser("index", help="Incrementally index memory files into SQLite.")
    index_parser.add_argument("--root", required=True, help="Initialized data root.")
    index_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )
    index_parser.add_argument("--dry-run", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search memories and indexed documents.")
    search_parser.add_argument("query")
    search_parser.add_argument("--root", required=True, help="Initialized data root.")
    search_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )
    search_parser.add_argument("--kind", choices=["all", "memory", "document"], default="all")
    search_parser.add_argument("--source-kind", choices=["chatgpt", "manual", "literature", "manuscript", "journal"])
    search_parser.add_argument("--project")
    search_parser.add_argument("--workspace")
    search_parser.add_argument("--status")
    search_parser.add_argument("--include-unassigned", action="store_true")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    doctor_parser = subparsers.add_parser("doctor", help="Inspect data root and derived SQLite index health.")
    doctor_parser.add_argument("--root", required=True, help="Initialized data root.")
    doctor_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )
    doctor_parser.add_argument("--json", action="store_true")

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
        if args.command == "db-init":
            summary = db_init(args)
            if summary["already"]:
                print("Database already initialized")
            else:
                print("Database initialized")
            print(f"State directory: {summary['state_dir']}")
            print(f"Database: {summary['database']}")
            print(f"Schema version: {summary['version']}")
            print("FTS5: available")
            print("Tables: " + ", ".join(summary["tables"]))
            return 0
        if args.command == "db-rebuild":
            summary = db_rebuild(args)
            print("Database rebuilt")
            print(f"Database: {summary['database']}")
            print(f"Schema version: {summary['version']}")
            print(f"Memories added: {summary['memories']['added']}")
            print(f"Documents added: {summary['documents']['added']}")
            return 0
        if args.command == "index":
            summary = index_store(args)
            if summary["dry_run"]:
                print("Dry run: no database changes")
                print(f"Would add: {summary['added']}")
                print(f"Would update: {summary['updated']}")
                print(f"Would delete: {summary['deleted']}")
                print(f"Would add documents: {summary['documents']['added']}")
                print(f"Would update documents: {summary['documents']['updated']}")
                print(f"Would delete documents: {summary['documents']['deleted']}")
            else:
                print("Index complete")
                print(f"Added: {summary['added']}")
                print(f"Updated: {summary['updated']}")
                print(f"Deleted: {summary['deleted']}")
                print(f"Documents added: {summary['documents']['added']}")
                print(f"Documents updated: {summary['documents']['updated']}")
                print(f"Documents deleted: {summary['documents']['deleted']}")
            print(f"Unchanged: {summary['unchanged']}")
            print(f"Documents unchanged: {summary['documents']['unchanged']}")
            print(f"Database: {summary['database']}")
            return 0
        if args.command == "search":
            rows = search_store(args)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    source = f" source={row['source_kind']}" if row.get("source_kind") else ""
                    project = f" project={row['project']}" if row.get("project") else ""
                    print(f"{row['id']}  {row['title']}  kind={row['kind']}{source}{project}")
                    if row.get("excerpt"):
                        print(f"  {row['excerpt']}")
                    print(f"  {row['relative_path']}")
                print(f"Results: {len(rows)}")
            return 0
        if args.command == "doctor":
            summary = doctor_store(args)
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print(f"Data root: {summary['data_root']}")
                print(f"SQLite initialized: {summary['sqlite_initialized']}")
                print(f"Schema version: {summary['schema_version']}")
                print(f"WAL: {summary['wal']}")
                print(f"Memory index fresh: {summary['memory_index_fresh']}")
                print(f"Document index fresh: {summary['document_index_fresh']}")
                print(f"Hash mismatches: {len(summary['hash_mismatches'])}")
                print(f"Manual orphan raw: {len(summary['manual_orphan_raw'])}")
                print(f"Manual orphan text: {len(summary['manual_orphan_text'])}")
                print(f"Errors: {len(summary['errors'])}")
            return 1 if summary["errors"] else 0
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
