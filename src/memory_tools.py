import argparse
import hashlib
import html
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
IMPORTER_VERSION = 2
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".rtf", ".pdf", ".docx"}


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


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_conversation_name(archive):
    names = [name for name in archive.namelist() if Path(name).name.lower() == "conversations.json"]
    if not names:
        raise ValueError("conversations.json was not found in the export ZIP.")
    if len(names) > 1:
        raise ValueError("Multiple conversations.json files were found in the export ZIP.")
    return names[0]


def _valid_conversation(conversation):
    if not isinstance(conversation, dict):
        return False
    if not (conversation.get("id") or conversation.get("conversation_id")):
        return False
    mapping = conversation.get("mapping")
    return isinstance(mapping, dict) and bool(mapping)


def preflight_chatgpt_export(zip_path):
    zip_path = Path(zip_path).expanduser()
    if not zip_path.exists():
        raise ValueError("ChatGPT export ZIP does not exist.")
    if zip_path.is_symlink():
        raise ValueError("ChatGPT export ZIP must not be a symbolic link.")
    report = {
        "zip_path": str(zip_path),
        "zip_sha256": _file_sha256(zip_path),
        "zip_valid": False,
        "conversation_count": 0,
        "message_count": 0,
        "invalid_conversations": 0,
        "warnings": [],
        "conversations": [],
    }
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"ZIP CRC check failed for {bad}.")
            data = json.loads(archive.read(_zip_conversation_name(archive)).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid ChatGPT export ZIP.") from exc
    if not isinstance(data, list):
        raise ValueError("conversations.json must contain a list.")
    report["zip_valid"] = True
    report["conversation_count"] = len(data)
    valid = []
    for index, conversation in enumerate(data):
        if not _valid_conversation(conversation):
            report["invalid_conversations"] += 1
            report["warnings"].append(f"invalid conversation at index {index}")
            continue
        _, message_count = _render_conversation(conversation)
        report["message_count"] += message_count
        valid.append(conversation)
    report["conversations"] = valid
    return report


def _load_conversations(zip_path):
    return preflight_chatgpt_export(zip_path)["conversations"]


def _write_import_report(root, kind, input_sha256, report):
    stamp = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    relative = Path("exports/import_reports") / f"{stamp}-{kind}-{input_sha256[:16]}.json"
    target = root / relative
    safe_report = dict(report)
    safe_report.pop("conversations", None)
    safe_report["report_path"] = relative.as_posix()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return relative.as_posix()


def _atomic_write_bytes(path, data):
    tmp = path.with_name(f".tmp-{path.name}-{hashlib.sha256(data).hexdigest()[:16]}")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _manual_text(path):
    suffix = path.suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        raise ValueError("Manual import supports PDF/DOCX/RTF/HTML/TXT/Markdown/CSV/JSON only when text is directly readable.")
    text = path.read_text(encoding="utf-8")
    if suffix in {".html", ".htm"}:
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    elif suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    return re.sub(r"\s+\n", "\n", text).strip()


def import_manual(args):
    root = _require_data_root(args.root)
    source = Path(args.path).expanduser()
    if not source.exists():
        raise ValueError("Manual import source does not exist.")
    if source.is_symlink():
        raise ValueError("Manual import source must not be a symbolic link.")
    if not source.is_file():
        raise ValueError("Manual import source must be a file.")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = _manual_text(source)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    relative_base = Path(f"{now.year:04d}/{now.month:02d}/{digest[:16]}-{safe_slug(source.stem)}")
    raw_relative = Path("imports/manual/raw") / relative_base.with_suffix(source.suffix.lower())
    text_relative = Path("imports/manual/text") / relative_base.with_suffix(".md")
    title = source.stem or source.name
    markdown = "\n".join(
        [
            "---",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"source_name: {json.dumps(source.name, ensure_ascii=False)}",
            f"source_sha256: {json.dumps(digest)}",
            f"imported_at: {json.dumps(now.isoformat())}",
            'source: "manual_import"',
            "---",
            "",
            f"# {title}",
            "",
            text,
            "",
        ]
    )
    report = {
        "format_version": 1,
        "importer_version": IMPORTER_VERSION,
        "kind": "manual",
        "input_path": str(source),
        "input_sha256": digest,
        "imported_at": now.isoformat(),
        "new": 0 if (root / raw_relative).exists() and (root / text_relative).exists() else 1,
        "updated": 0,
        "duplicate": 1 if (root / raw_relative).exists() and (root / text_relative).exists() else 0,
        "failed": 0,
        "written_paths": [raw_relative.as_posix(), text_relative.as_posix()],
        "index_result": "not_run",
        "restore_suggestion": "",
    }
    if not args.dry_run:
        (root / raw_relative).parent.mkdir(parents=True, exist_ok=True)
        (root / text_relative).parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(root / raw_relative, raw)
        atomic_write_text(root / text_relative, markdown)
        report["report_path"] = _write_import_report(root, "manual", digest, report)
    print(f"Raw: {raw_relative.as_posix()}")
    print(f"Text: {text_relative.as_posix()}")
    if args.dry_run:
        print("Dry run: no files changed")
    else:
        print(f"Report: {report['report_path']}")
    return report


def import_chatgpt(args):
    root = _require_data_root(args.root)
    zip_path = Path(args.zip).expanduser()
    preflight = preflight_chatgpt_export(zip_path)
    conversations = preflight["conversations"]
    output_root = root / "imports" / "chatgpt" / "conversations"
    manifest_path = root / "imports" / "chatgpt" / "import_manifest.json"
    records = []
    created = updated = unchanged = 0

    for conversation in conversations:
        conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "unknown")
        title = (conversation.get("title") or "Untitled conversation").strip()
        stamp = datetime.fromtimestamp(conversation.get("create_time") or 0, timezone.utc)
        relative = Path(f"{stamp.year:04d}/{stamp.month:02d}/{safe_slug(conversation_id)}.md")
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

    raw_only = unchanged if not getattr(args, "restore_recent", False) else 0
    report = {
        "format_version": 1,
        "importer_version": IMPORTER_VERSION,
        "kind": "chatgpt",
        "input_path": str(zip_path),
        "input_sha256": preflight["zip_sha256"],
        "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "zip_path": preflight["zip_path"],
        "zip_sha256": preflight["zip_sha256"],
        "zip_valid": preflight["zip_valid"],
        "conversation_count": preflight["conversation_count"],
        "message_count": preflight["message_count"],
        "invalid_conversations": preflight["invalid_conversations"],
        "new": created,
        "updated": updated,
        "unchanged": unchanged,
        "raw_only": raw_only,
        "warnings": preflight["warnings"],
        "failed": preflight["invalid_conversations"],
        "written_paths": [record["path"] for record in records],
        "index_result": "not_run",
        "restore_suggestion": "Run import-chatgpt --restore-recent only if a future recent pipeline explicitly supports it.",
    }
    manifest = {
        "format_version": 1,
        "source": Path(args.zip).name,
        "conversations": sorted(records, key=lambda item: item["conversation_id"]),
    }
    if not args.dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        report["report_path"] = _write_import_report(root, "chatgpt", preflight["zip_sha256"], report)

    print(f"Conversations: {len(records)}")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")
    print(f"Invalid conversations: {preflight['invalid_conversations']}")
    print(f"Raw only: {raw_only}")
    if args.dry_run:
        print("Dry run: no files changed")
    else:
        print("Manifest: imports/chatgpt/import_manifest.json")
        print(f"Report: {report['report_path']}")
    return report


def build_parser():
    parser = argparse.ArgumentParser(description="Search memory and import local evidence files.")
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
    import_parser.add_argument("--restore-recent", action="store_true")

    manual_parser = subparsers.add_parser("import-manual", help="Import a local readable file as raw evidence plus text sidecar.")
    manual_parser.add_argument("--path", required=True)
    manual_parser.add_argument("--root", required=True)
    manual_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "search":
            search(args)
        elif args.command == "import-chatgpt":
            import_chatgpt(args)
        else:
            import_manual(args)
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
