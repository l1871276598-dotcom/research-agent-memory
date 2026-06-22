import argparse
import json
import re
import sys
import uuid
from datetime import date
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
    if not root.exists() or not marker.exists():
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


def render_memory(record):
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

    lines.append("tags:")
    for tag in record["tags"]:
        lines.append(f"  - {_quoted(tag)}")
    if not record["tags"]:
        lines[-1] = "tags: []"

    lines.append("content: |-")
    content_lines = record["content"].splitlines() or [""]
    for line in content_lines:
        lines.append(f"  {line}")
    lines.extend(
        [
            "---",
            "",
            f"# {record['title']}",
            "",
            "该记忆的结构化内容保存在 front matter 的 content 字段中。",
            "",
        ]
    )
    return "\n".join(lines)


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
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
