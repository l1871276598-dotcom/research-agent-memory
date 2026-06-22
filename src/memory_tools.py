import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from memory import (
    _require_data_root,
    atomic_write_text,
    check_state_dir,
    default_state_dir,
    inspect_database,
    safe_slug,
)

CJK_RUN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]+")


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


def search(args):
    root = _require_data_root(args.root)
    _, db = check_state_dir(root, args.state_dir or default_state_dir())
    if not db.is_file() or not inspect_database(db).get("initialized"):
        raise ValueError("Database is not initialized. Run db-init and index first.")

    conditions = ["memory_fts MATCH ?"]
    values = [_fts_query(args.query)]
    for column in ("type", "project", "workspace", "status"):
        value = getattr(args, column)
        if value:
            conditions.append(f"m.{column} = ?")
            values.append(value)
    values.append(args.limit)

    sql = f"""
        SELECT m.id, m.title, m.type, m.project, m.status, m.workspace,
               m.confidentiality, m.updated, m.relative_path,
               snippet(memory_fts, 2, '[', ']', '…', 20) AS snippet,
               bm25(memory_fts) AS rank
        FROM memory_fts
        JOIN memories AS m ON m.id = memory_fts.id
        WHERE {' AND '.join(conditions)}
        ORDER BY rank, m.updated DESC, m.id
        LIMIT ?
    """
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(sql, values)]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            project = f" project={row['project']}" if row["project"] else ""
            print(f"{row['id']}  {row['title']}  type={row['type']}{project}")
            if row["snippet"]:
                print(f"  {row['snippet']}")
            print(f"  {row['relative_path']}")
        print(f"Results: {len(rows)}")
    return len(rows)


def _iso_time(value):
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat()


def _message_text(message):
    content = (message or {}).get("content") or {}
    parts = content.get("parts") or []
    output = []
    for part in parts:
        if isinstance(part, str):
            output.append(part)
        elif isinstance(part, dict):
            text = part.get("text") or part.get("content")
            if isinstance(text, str):
                output.append(text)
    return "\n\n".join(value.strip() for value in output if value and value.strip())


def _active_messages(conversation):
    mapping = conversation.get("mapping") or {}
    node_id = conversation.get("current_node")
    selected = []
    seen = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = mapping.get(node_id) or {}
        message = node.get("message")
        if message:
            selected.append(message)
        node_id = node.get("parent")
    if selected:
        return list(reversed(selected))
    return sorted(
        (node.get("message") for node in mapping.values() if node.get("message")),
        key=lambda item: item.get("create_time") or 0,
    )


def _render_conversation(conversation):
    title = (conversation.get("title") or "Untitled conversation").strip()
    conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "unknown")
    created = _iso_time(conversation.get("create_time"))
    updated = _iso_time(conversation.get("update_time"))
    lines = [
        "---",
        f"conversation_id: {json.dumps(conversation_id, ensure_ascii=False)}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"created: {json.dumps(created, ensure_ascii=False)}",
        f"updated: {json.dumps(updated, ensure_ascii=False)}",
        'source: "chatgpt_export"',
        "---",
        "",
        f"# {title}",
        "",
    ]
    count = 0
    for message in _active_messages(conversation):
        text = _message_text(message)
        if not text:
            continue
        role = ((message.get("author") or {}).get("role") or "unknown").capitalize()
        created_at = _iso_time(message.get("create_time"))
        lines.extend([f"## {role}", ""])
        if created_at:
            lines.extend([f"_Time: {created_at}_", ""])
        lines.extend([text, ""])
        count += 1
    return "\n".join(lines).rstrip() + "\n", count


def _load_conversations(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if Path(name).name.lower() == "conversations.json"]
            if not names:
                raise ValueError("conversations.json was not found in the export ZIP.")
            data = json.loads(archive.read(sorted(names, key=len)[0]).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid ChatGPT export ZIP.") from exc
    if not isinstance(data, list):
        raise ValueError("conversations.json must contain a list.")
    return data


def import_chatgpt(args):
    root = _require_data_root(args.root)
    conversations = _load_conversations(Path(args.zip).expanduser())
    output_root = root / "imports" / "chatgpt" / "conversations"
    manifest_path = root / "imports" / "chatgpt" / "import_manifest.json"
    records = []
    created = updated = unchanged = 0

    for conversation in conversations:
        conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "unknown")
        title = (conversation.get("title") or "Untitled conversation").strip()
        stamp = datetime.fromtimestamp(conversation.get("create_time") or 0, timezone.utc)
        relative = Path(f"{stamp.year:04d}/{stamp.month:02d}/{safe_slug(conversation_id)}-{safe_slug(title)}.md")
        target = output_root / relative
        content, message_count = _render_conversation(conversation)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if existing == digest:
            unchanged += 1
        elif target.exists():
            updated += 1
        else:
            created += 1
        if not args.dry_run and existing != digest:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, content)
        records.append(
            {
                "conversation_id": conversation_id,
                "title": title,
                "path": (Path("imports/chatgpt/conversations") / relative).as_posix(),
                "sha256": digest,
                "message_count": message_count,
            }
        )

    manifest = {
        "format_version": 1,
        "source": Path(args.zip).name,
        "conversations": sorted(records, key=lambda item: item["conversation_id"]),
    }
    if not args.dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print(f"Conversations: {len(records)}")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")
    if args.dry_run:
        print("Dry run: no files changed")
    else:
        print("Manifest: imports/chatgpt/import_manifest.json")
    return manifest


def build_parser():
    parser = argparse.ArgumentParser(description="Search memory and import ChatGPT exports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search the local SQLite FTS5 index.")
    search_parser.add_argument("query")
    search_parser.add_argument("--root", required=True)
    search_parser.add_argument("--state-dir", default=str(default_state_dir()))
    search_parser.add_argument("--type")
    search_parser.add_argument("--project")
    search_parser.add_argument("--workspace")
    search_parser.add_argument("--status")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    import_parser = subparsers.add_parser("import-chatgpt", help="Archive conversations from an official export ZIP.")
    import_parser.add_argument("--zip", required=True)
    import_parser.add_argument("--root", required=True)
    import_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "search":
            search(args)
        else:
            import_chatgpt(args)
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
