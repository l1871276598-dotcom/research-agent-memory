import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
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
    "memory/recent",
    "imports/chatgpt/conversations",
    "imports/manual/inbox",
    "imports/manual/raw",
    "imports/manual/quarantine",
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
    "imports/manual/import_manifest.json": json.dumps(
        {"format_version": 1, "imports": []},
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    "literature/literature_matrix.csv": (
        "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags\n"
    ),
}
DB_NAME = "memory.sqlite"
DB_SCHEMA_VERSION = 2
STATE_DIR_ERROR = "SQLite state directory must be local and outside iCloud and the code repository."
MEMORIES_COLUMNS = [
    "id",
    "schema",
    "tier",
    "type",
    "title",
    "content",
    "body",
    "tags",
    "aliases",
    "status",
    "scope",
    "workspace",
    "confidentiality",
    "source",
    "confidence",
    "source_id",
    "source_path",
    "source_sha256",
    "context_id",
    "project",
    "valid_from",
    "valid_until",
    "created",
    "updated",
    "relative_path",
    "sha256",
    "metadata_json",
]
INDEX_STATE_COLUMNS = ["relative_path", "sha256", "mtime_ns", "indexed_at"]
LINK_COLUMNS = [
    "source_id",
    "target_id",
    "target_ref",
    "relation",
    "source_kind",
    "target_heading",
    "target_block",
    "context",
    "resolved",
]
AUDIT_COLUMNS = [
    "recent_id",
    "relative_path",
    "source_sha256",
    "status",
    "result_json",
    "distilled_at",
    "delete_after",
    "deleted_at",
    "error",
]
REQUIRED_INDEXES = {
    "idx_memories_type",
    "idx_memories_project",
    "idx_memories_context",
    "idx_memories_workspace_status",
}
CJK_RUN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]+")
LOCK_NAME = "memory-write.lock"
_LOCK_STACK = []
RECENT_DIR = "memory/recent"
RECENT_BODY_DAYS = 30
DELETION_GRACE_DAYS = 7
RELATION_CHOICES = {
    "belongs_to",
    "applies_to",
    "depends_on",
    "derived_from",
    "supports",
    "contradicts",
    "supersedes",
    "superseded_by",
    "related_to",
}
AGENT_FIELDS = {
    "schema",
    "tier",
    "subject",
    "predicate",
    "object",
    "aliases",
    "relations",
    "source_id",
    "source_path",
    "source_sha256",
    "source_refs",
    "retention_until",
    "distillation_status",
    "distilled_at",
    "protected",
    "original_name",
    "media_type",
    "extractor",
    "imported_at",
}
ALLOWED_FIELDS = ALLOWED_FIELDS | AGENT_FIELDS
LIST_FIELDS = LIST_FIELDS | {"aliases", "relations", "source_refs"}


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
        "schema",
        "id",
        "type",
        "tier",
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
        "subject",
        "predicate",
        "object",
        "source_id",
        "source_path",
        "source_sha256",
        "retention_until",
        "distillation_status",
        "distilled_at",
        "protected",
        "original_name",
        "media_type",
        "extractor",
        "imported_at",
    ]:
        if field in record:
            lines.append(f"{field}: {_quoted(record[field])}")

    for field in ["aliases", "relations", "source_refs", "supersedes", "superseded_by", "tags"]:
        if field not in record:
            continue
        values = record.get(field, [])
        lines.append(f"{field}:")
        for value in values:
            lines.append(f"  - {_quoted(value)}")
        if not values:
            lines[-1] = f"{field}: []"

    if "content" in record:
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


def parse_memory_document(path):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, "", ["file is not valid UTF-8"]
    except OSError as exc:
        return None, "", [f"cannot read file: {exc}"]
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, "", ["missing opening front matter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, "", ["unclosed front matter"]
    record, errors = parse_front_matter(path)
    body = "\n".join(lines[end + 1 :]).strip()
    return record, body, errors


def _real_date(value):
    if not isinstance(value, str):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _relation_parts(value):
    if not isinstance(value, str) or ":" not in value:
        return None, None
    relation, target = value.split(":", 1)
    return relation.strip(), target.strip()


def validate_record(record, path, rel_path, expected_type):
    errors = []
    schema = record.get("schema", "memory/v1")
    is_recent = schema == "recent/v1" or expected_type == "recent"
    required = [
        "schema",
        "id",
        "type",
        "tier",
        "title",
        "created",
        "updated",
        "status",
        "source",
        "source_id",
        "source_path",
        "source_sha256",
        "retention_until",
        "distillation_status",
        "protected",
        "content",
    ] if is_recent else REQUIRED_FIELDS
    if schema == "memory/v2":
        required = list(dict.fromkeys(required + ["schema", "subject", "predicate", "object", "relations"]))
    for field in required:
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
    elif not (path.name.startswith(f"{memory_id}-") or path.name == f"{memory_id}.md"):
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
    if schema not in {"memory/v1", "memory/v2", "recent/v1"}:
        errors.append("schema has invalid value")
    if is_recent and record.get("tier") != "recent":
        errors.append("recent memory requires tier recent")
    if not is_recent and "tier" in record and record.get("tier") not in {"long", "recent"}:
        errors.append("tier has invalid value")

    for field in ("title", "source", "subject", "predicate", "object", "source_id", "source_path", "source_sha256"):
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
    for value in record.get("relations", []) if isinstance(record.get("relations"), list) else []:
        relation, target = _relation_parts(value)
        if not relation or not target:
            errors.append("relation must use relation:target format")
        elif relation not in RELATION_CHOICES:
            errors.append("relation has invalid predicate")

    memory_type = record.get("type")
    scope = record.get("scope")
    if not is_recent and memory_type != expected_type:
        errors.append("type does not match directory")
    if is_recent:
        return [(rel_path, message) for message in errors]
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
    for relative_dir in list(TYPE_DIRS.values()) + [RECENT_DIR]:
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
        if expected_type is None and (rel_path == RECENT_DIR or rel_path.startswith(RECENT_DIR + "/")):
            expected_type = "recent"
        record, body, parse_errors = parse_memory_document(path)
        for message in parse_errors:
            errors.append((rel_path, message))
        if record is None:
            continue
        errors.extend(validate_record(record, path, rel_path, expected_type))
        records.append({"relative_path": rel_path, "record": record, "body": body, "path": path})

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
    if rel_path == RECENT_DIR or rel_path.startswith(RECENT_DIR + "/"):
        return "recent"
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


@contextlib.contextmanager
def write_lock(root, state_dir):
    state, _ = check_state_dir(_require_data_root(root), state_dir or default_state_dir())
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / LOCK_NAME
    if lock_path in _LOCK_STACK:
        _LOCK_STACK.append(lock_path)
        try:
            yield
        finally:
            _LOCK_STACK.pop()
        return
    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _LOCK_STACK.append(lock_path)
        try:
            yield
        finally:
            _LOCK_STACK.pop()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    lock_path.unlink(missing_ok=True)


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
            schema TEXT NOT NULL DEFAULT 'memory/v1',
            tier TEXT NOT NULL DEFAULT 'long',
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            aliases TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            scope TEXT NOT NULL,
            workspace TEXT NOT NULL,
            confidentiality TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence TEXT NOT NULL,
            source_id TEXT,
            source_path TEXT,
            source_sha256 TEXT,
            context_id TEXT,
            project TEXT,
            valid_from TEXT,
            valid_until TEXT,
            created TEXT NOT NULL,
            updated TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
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
            body,
            tags,
            aliases,
            tokenize = 'unicode61'
        );
        CREATE TABLE index_state (
            relative_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE links (
            source_id TEXT NOT NULL,
            target_id TEXT,
            target_ref TEXT NOT NULL,
            relation TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            target_heading TEXT,
            target_block TEXT,
            context TEXT,
            resolved INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_links_source ON links(source_id);
        CREATE INDEX idx_links_target ON links(target_id);
        CREATE INDEX idx_links_ref ON links(target_ref);
        CREATE TABLE distillation_audit (
            recent_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            distilled_at TEXT,
            delete_after TEXT,
            deleted_at TEXT,
            error TEXT
        );
        PRAGMA user_version = 2;
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
            WHERE name IN ('memories', 'memory_fts', 'index_state', 'links', 'distillation_audit')
            """
        )
    }


def _inspect_database_connection(conn, verify_wal=True):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0] if verify_wal else "wal"
    tables = _target_tables(conn)
    if version != DB_SCHEMA_VERSION:
        if version == 0 and not tables:
            return {"initialized": False, "version": version}
        raise ValueError("Incompatible SQLite schema version.")
    if str(mode).lower() != "wal":
        raise ValueError("SQLite journal_mode WAL could not be verified.")
    if set(tables) != {"memories", "memory_fts", "index_state", "links", "distillation_audit"}:
        raise ValueError("SQLite schema is incomplete.")
    if "CREATE VIRTUAL TABLE" not in tables["memory_fts"][1].upper():
        raise ValueError("SQLite FTS5 table is missing.")
    if _table_columns(conn, "memories") != MEMORIES_COLUMNS:
        raise ValueError("SQLite memories table columns do not match.")
    if _table_columns(conn, "index_state") != INDEX_STATE_COLUMNS:
        raise ValueError("SQLite index_state table columns do not match.")
    if _table_columns(conn, "links") != LINK_COLUMNS:
        raise ValueError("SQLite links table columns do not match.")
    if _table_columns(conn, "distillation_audit") != AUDIT_COLUMNS:
        raise ValueError("SQLite distillation_audit table columns do not match.")
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_memories_%'"
        )
    }
    if not REQUIRED_INDEXES.issubset(indexes):
        raise ValueError("SQLite memories indexes are incomplete.")
    return {"initialized": True, "version": version}


def inspect_database(db):
    try:
        with sqlite3.connect(db) as conn:
            return _inspect_database_connection(conn)
    except sqlite3.DatabaseError as exc:
        raise ValueError("Invalid SQLite database.") from exc


def database_has_wal_header(db):
    try:
        header = db.read_bytes()[:100]
    except OSError as exc:
        raise ValueError("Invalid SQLite database.") from exc
    return len(header) >= 100 and header[18] == 2 and header[19] == 2


def _checkpoint_and_close(conn):
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def sqlite_sidecars(path):
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]


def _remove_sqlite_sidecars(path):
    for sidecar in sqlite_sidecars(path):
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
        "tables": ["memories", "memory_fts", "index_state", "links", "distillation_audit"],
    }


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


def _record_aliases(record):
    aliases = record.get("aliases", [])
    return aliases if isinstance(aliases, list) else []


def _record_schema(record):
    return record.get("schema", "memory/v1")


def _record_tier(record):
    if _record_schema(record) == "recent/v1":
        return "recent"
    return record.get("tier", "long")


def _metadata_json(record):
    structured = {
        key: value
        for key, value in record.items()
        if key
        in {
            "subject",
            "predicate",
            "object",
            "relations",
            "source_id",
            "source_path",
            "source_sha256",
            "source_refs",
            "retention_until",
            "distillation_status",
            "distilled_at",
            "protected",
            "original_name",
            "media_type",
            "extractor",
            "imported_at",
        }
    }
    return json.dumps(structured, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_to_db_values(item):
    record = item["record"]
    return {
        "id": record["id"],
        "schema": _record_schema(record),
        "tier": _record_tier(record),
        "type": record["type"],
        "title": record["title"],
        "content": record["content"],
        "body": item.get("body", ""),
        "tags": json.dumps(_record_tags(record), ensure_ascii=False, separators=(",", ":")),
        "aliases": json.dumps(_record_aliases(record), ensure_ascii=False, separators=(",", ":")),
        "status": record["status"],
        "scope": record.get("scope", "global"),
        "workspace": record.get("workspace", "personal"),
        "confidentiality": record.get("confidentiality", "personal"),
        "source": record["source"],
        "confidence": record.get("confidence", "inferred"),
        "source_id": record.get("source_id"),
        "source_path": record.get("source_path"),
        "source_sha256": record.get("source_sha256"),
        "context_id": record.get("context_id"),
        "project": record.get("project"),
        "valid_from": record.get("valid_from"),
        "valid_until": record.get("valid_until"),
        "created": record["created"],
        "updated": record["updated"],
        "relative_path": item["relative_path"],
        "sha256": item["sha256"],
        "metadata_json": _metadata_json(record),
    }


def _fts_values(item):
    record = item["record"]
    return (
        record["id"],
        normalize_fts_text(record["title"]),
        normalize_fts_text(record["content"]),
        normalize_fts_text(item.get("body", "")),
        normalize_fts_text(" ".join(_record_tags(record))),
        normalize_fts_text(" ".join(_record_aliases(record))),
    )


WIKILINK_PATTERN = re.compile(r"!?\[\[([^\]|#\^]+)(?:#([^\]|\^]+))?(?:\^([^\]|]+))?(?:\|[^\]]*)?\]\]")


def _identity_maps(items):
    ids = {}
    aliases = {}
    for item in items:
        record = item["record"]
        memory_id = record.get("id")
        if isinstance(memory_id, str):
            ids[memory_id] = memory_id
        for alias in _record_aliases(record):
            aliases.setdefault(alias, memory_id)
    return ids, aliases


def _resolve_ref(target_ref, ids, aliases):
    if target_ref in ids:
        return ids[target_ref], 1
    if target_ref in aliases:
        return aliases[target_ref], 1
    return None, 0


def _links_for_item(item, ids, aliases):
    record = item["record"]
    source_id = record["id"]
    links = []
    seen = set()
    for value in record.get("relations", []) if isinstance(record.get("relations"), list) else []:
        relation, target_ref = _relation_parts(value)
        if relation not in RELATION_CHOICES or not target_ref:
            continue
        target_id, resolved = _resolve_ref(target_ref, ids, aliases)
        key = (source_id, target_ref, relation, "frontmatter", None, None)
        if key not in seen:
            links.append((source_id, target_id, target_ref, relation, "frontmatter", None, None, value, resolved))
            seen.add(key)
    for match in WIKILINK_PATTERN.finditer(item.get("body", "")):
        target_ref = match.group(1).strip()
        heading = (match.group(2) or "").strip() or None
        block = (match.group(3) or "").strip() or None
        target_id, resolved = _resolve_ref(target_ref, ids, aliases)
        key = (source_id, target_ref, "related_to", "body", heading, block)
        if key not in seen:
            links.append((source_id, target_id, target_ref, "related_to", "body", heading, block, match.group(0), resolved))
            seen.add(key)
    return links


def _current_index_items(root, records):
    items = []
    for item in records:
        path = item["path"]
        raw = path.read_bytes()
        items.append(
            {
                "relative_path": item["relative_path"],
                "record": item["record"],
                "body": item.get("body", ""),
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mtime_ns": path.stat().st_mtime_ns,
            }
        )
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
        "INSERT INTO memory_fts(id, title, content, body, tags, aliases) VALUES (?, ?, ?, ?, ?, ?)",
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


def rebuild_links(conn, items):
    ids, aliases = _identity_maps(items)
    conn.execute("DELETE FROM links")
    for item in items:
        conn.executemany(
            """
            INSERT INTO links(source_id, target_id, target_ref, relation, source_kind, target_heading, target_block, context, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _links_for_item(item, ids, aliases),
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


def apply_index_changes(conn, plan, items, expected_count):
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

    rebuild_links(conn, items)
    check_index_consistency(conn, expected_count)


def index_store(args):
    root, records, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Index aborted because validation failed."]))

    state_dir = args.state_dir if args.state_dir else default_state_dir()
    state, db = check_state_dir(root, state_dir)
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init first.")
    with write_lock(root, state):
        summary = inspect_database(db)
        if not summary["initialized"]:
            raise ValueError("Database is not initialized. Run db-init first.")

        items = _current_index_items(root, records)
        with sqlite3.connect(db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000")
            plan = plan_index_changes(conn, items)
            result = {
                "added": len(plan["added"]),
                "updated": len(plan["updated"]),
                "deleted": len(plan["deleted"]),
                "unchanged": len(plan["unchanged"]),
                "database": db,
                "dry_run": args.dry_run,
            }
            if args.dry_run:
                return result
            try:
                conn.execute("BEGIN")
                apply_index_changes(conn, plan, items, len(items))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        checkpoint_database(db)
        return result


def db_rebuild(args):
    root, records, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Database rebuild aborted because validation failed."]))
    check_fts5()
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    state, db = check_state_dir(root, state_dir)
    state.mkdir(parents=True, exist_ok=True)
    with write_lock(root, state):
        tmp = state / f".tmp-{DB_NAME}-{uuid.uuid4().hex}"
        try:
            conn = sqlite3.connect(tmp)
            _configure_sqlite(conn)
            _create_schema(conn)
            items = _current_index_items(root, records)
            try:
                conn.execute("BEGIN")
                plan = plan_index_changes(conn, items)
                apply_index_changes(conn, plan, items, len(items))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                _checkpoint_and_close(conn)
            inspect_database(tmp)
            tmp.replace(db)
            _remove_sqlite_sidecars(tmp)
            checkpoint_database(db)
        except Exception:
            tmp.unlink(missing_ok=True)
            _remove_sqlite_sidecars(tmp)
            raise
    return {"database": db, "records": len(records), "version": DB_SCHEMA_VERSION}


def extract_search_terms(query):
    cjk_matches = list(CJK_RUN.finditer(query))
    if any(len(match.group(0)) == 1 for match in cjk_matches):
        raise ValueError("Chinese search terms must contain at least 2 consecutive characters.")
    terms = []
    for match in cjk_matches:
        run = match.group(0)
        if len(run) == 2:
            terms.append(run)
        else:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    without_cjk = CJK_RUN.sub(" ", query)
    terms.extend(re.findall(r"[A-Za-z0-9_-]+", without_cjk))
    return terms


def build_fts_query(query):
    terms = extract_search_terms(query)
    if not terms:
        raise ValueError("Search query has no valid terms.")
    return " AND ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)


def _readonly_connection(db):
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def check_search_freshness(conn, items):
    expected = len(items)
    state = _database_index_state(conn)
    current_paths = {item["relative_path"] for item in items}
    current_ids = {item["record"]["id"] for item in items}

    counts = {
        "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "index_state": conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0],
        "fts_distinct": conn.execute("SELECT COUNT(DISTINCT id) FROM memory_fts").fetchone()[0],
    }
    if any(value != expected for value in counts.values()):
        raise ValueError("Search aborted because the index is stale. Run index first.")
    if conn.execute("SELECT id FROM memory_fts GROUP BY id HAVING COUNT(*) != 1").fetchone():
        raise ValueError("Search aborted because the index is stale. Run index first.")
    if set(state["index_state"]) != current_paths:
        raise ValueError("Search aborted because the index is stale. Run index first.")
    if set(state["memories_by_id"]) != current_ids:
        raise ValueError("Search aborted because the index is stale. Run index first.")

    for item in items:
        state_row = state["index_state"].get(item["relative_path"])
        memory_row = state["memories_by_id"].get(item["record"]["id"])
        if not state_row or state_row["sha256"] != item["sha256"]:
            raise ValueError("Search aborted because the index is stale. Run index first.")
        if not memory_row or memory_row["relative_path"] != item["relative_path"]:
            raise ValueError("Search aborted because the index is stale. Run index first.")
        if state["fts_counts"].get(item["record"]["id"]) != 1:
            raise ValueError("Search aborted because the index is stale. Run index first.")


def _search_statuses(args):
    statuses = list(args.status or ["active"])
    if args.include_historical and "historical" not in statuses:
        statuses.append("historical")
    return statuses


def _allowed_confidentiality(args):
    if args.include_restricted and args.workspace != "work":
        raise ValueError("--include-restricted requires --workspace work.")
    if args.workspace == "personal":
        return ["public", "personal"]
    values = ["public", "personal", "internal"]
    if args.include_restricted:
        values.append("restricted")
    return values


def _validate_search_args(args):
    if not (1 <= args.limit <= 100):
        raise ValueError("--limit must be between 1 and 100.")
    _allowed_confidentiality(args)


def make_excerpt(content, query):
    text = re.sub(r"\s+", " ", content or "").strip()
    if len(text) <= 180:
        return text
    fragments = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF]{2,}|[A-Za-z0-9_-]+", query)
    center = -1
    for fragment in fragments:
        center = text.lower().find(fragment.lower())
        if center >= 0:
            break
    if center < 0:
        start = 0
    else:
        start = max(0, center - 80)
    end = min(len(text), start + 180)
    start = max(0, end - 180)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "…" + excerpt[1:]
    if end < len(text):
        excerpt = excerpt[:-1] + "…"
    return excerpt


def _row_to_result(row, query):
    keys = [
        "id",
        "title",
        "type",
        "status",
        "scope",
        "workspace",
        "confidentiality",
        "project",
        "context_id",
        "updated",
        "relative_path",
        "score",
        "content",
    ]
    data = dict(zip(keys, row))
    return {
        "id": data["id"],
        "title": data["title"],
        "type": data["type"],
        "status": data["status"],
        "scope": data["scope"],
        "workspace": data["workspace"],
        "confidentiality": data["confidentiality"],
        "project": data["project"],
        "context_id": data["context_id"],
        "updated": data["updated"],
        "relative_path": data["relative_path"],
        "score": round(float(data["score"]), 6),
        "excerpt": make_excerpt(data["content"], query),
    }


def search_store(args):
    _validate_search_args(args)
    root, records, errors, _ = collect_validated_records(args.root)
    if errors:
        messages = [f"ERROR {rel_path}: {message}" for rel_path, message in errors]
        raise ValueError("\n".join(messages + ["Search aborted because validation failed."]))

    fts_query = build_fts_query(args.query)
    state_dir = args.state_dir if args.state_dir else default_state_dir()
    _, db = check_state_dir(root, state_dir)
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init first.")
    if any(sidecar.exists() for sidecar in sqlite_sidecars(db)):
        raise ValueError("Search aborted because the index is stale. Run index first.")
    if not database_has_wal_header(db):
        raise ValueError("SQLite journal_mode WAL could not be verified.")

    items = _current_index_items(root, records)
    try:
        with _readonly_connection(db) as conn:
            summary = _inspect_database_connection(conn, verify_wal=False)
            if not summary["initialized"]:
                raise ValueError("Database is not initialized. Run db-init first.")
            check_search_freshness(conn, items)

            where = ["memory_fts MATCH ?", "memories.workspace = ?"]
            params = [fts_query, args.workspace]
            confidentialities = _allowed_confidentiality(args)
            where.append("memories.confidentiality IN (" + ", ".join("?" for _ in confidentialities) + ")")
            params.extend(confidentialities)
            statuses = _search_statuses(args)
            where.append("memories.status IN (" + ", ".join("?" for _ in statuses) + ")")
            params.extend(statuses)
            for column, value in [("type", args.type), ("project", args.project), ("context_id", args.context_id)]:
                if value is not None:
                    where.append(f"memories.{column} = ?")
                    params.append(value)
            params.append(args.limit)
            rows = conn.execute(
                f"""
                SELECT
                    memories.id,
                    memories.title,
                    memories.type,
                    memories.status,
                    memories.scope,
                    memories.workspace,
                    memories.confidentiality,
                    memories.project,
                    memories.context_id,
                    memories.updated,
                    memories.relative_path,
                    bm25(memory_fts, 0.0, 5.0, 1.0, 1.0, 2.0, 4.0) AS score,
                    memories.content
                FROM memory_fts
                JOIN memories ON memories.id = memory_fts.id
                WHERE {" AND ".join(where)}
                ORDER BY score ASC, memories.updated DESC, memories.id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Search failed: {exc}") from exc

    results = [_row_to_result(row, args.query) for row in rows]
    return {
        "query": args.query,
        "workspace": args.workspace,
        "filters": {
            "type": args.type,
            "project": args.project,
            "context_id": args.context_id,
            "statuses": statuses,
            "include_restricted": args.include_restricted,
        },
        "count": len(results),
        "results": results,
    }


def render_search_text(summary):
    if not summary["results"]:
        return "Results: 0\n"
    lines = [f"Results: {summary['count']}", ""]
    for index, row in enumerate(summary["results"], start=1):
        lines.extend(
            [
                f"{index}. {row['title']}",
                f"   ID: {row['id']}",
                f"   Type: {row['type']}",
                f"   Status: {row['status']}",
                f"   Workspace: {row['workspace']}",
                f"   Confidentiality: {row['confidentiality']}",
                f"   Project: {row['project'] or '-'}",
                f"   Context: {row['context_id'] or '-'}",
                f"   Updated: {row['updated']}",
                f"   Path: {row['relative_path']}",
                f"   Score: {row['score']:.6f}",
                f"   Excerpt: {row['excerpt']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


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

    index_parser = subparsers.add_parser("index", help="Incrementally index memory files into SQLite.")
    index_parser.add_argument("--root", required=True, help="Initialized data root.")
    index_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )
    index_parser.add_argument("--dry-run", action="store_true")

    rebuild_parser = subparsers.add_parser("db-rebuild", help="Safely rebuild the local SQLite database.")
    rebuild_parser.add_argument("--root", required=True, help="Initialized data root.")
    rebuild_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )

    search_parser = subparsers.add_parser("search", help="Search indexed memory records.")
    search_parser.add_argument("query")
    search_parser.add_argument("--root", required=True, help="Initialized data root.")
    search_parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Local state directory for memory.sqlite.",
    )
    search_parser.add_argument("--workspace", default="personal", choices=WORKSPACE_CHOICES)
    search_parser.add_argument("--type", choices=TYPE_CHOICES)
    search_parser.add_argument("--project")
    search_parser.add_argument("--context-id", dest="context_id")
    search_parser.add_argument("--status", action="append", choices=STATUS_CHOICES)
    search_parser.add_argument("--include-historical", action="store_true")
    search_parser.add_argument("--include-restricted", action="store_true")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--json", action="store_true", dest="json_output")

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
        if args.command == "index":
            summary = index_store(args)
            if summary["dry_run"]:
                print("Dry run: no database changes")
                print(f"Would add: {summary['added']}")
                print(f"Would update: {summary['updated']}")
                print(f"Would delete: {summary['deleted']}")
            else:
                print("Index complete")
                print(f"Added: {summary['added']}")
                print(f"Updated: {summary['updated']}")
                print(f"Deleted: {summary['deleted']}")
            print(f"Unchanged: {summary['unchanged']}")
            print(f"Database: {summary['database']}")
            return 0
        if args.command == "db-rebuild":
            summary = db_rebuild(args)
            print("Database rebuilt")
            print(f"Records: {summary['records']}")
            print(f"Schema version: {summary['version']}")
            print(f"Database: {summary['database']}")
            return 0
        if args.command == "search":
            summary = search_store(args)
            if args.json_output:
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
            else:
                print(render_search_text(summary), end="")
            return 0
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
