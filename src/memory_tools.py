import argparse
import hashlib
import html
import json
import mimetypes
import os
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
    safe_slug,
    search_store,
)

IMPORTER_VERSION = 2
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".rtf"}
ARCHIVE_ONLY_SUFFIXES = {".pdf", ".docx"}
MANUAL_SUFFIXES = TEXT_SUFFIXES | ARCHIVE_ONLY_SUFFIXES


def search(args):
    delegated = argparse.Namespace(
        root=args.root,
        state_dir=args.state_dir,
        query=args.query,
        kind="memory",
        source_kind=None,
        type=getattr(args, "type", None),
        project=getattr(args, "project", None),
        context_id=None,
        workspace=getattr(args, "workspace", None),
        status=getattr(args, "status", None),
        as_of=None,
        include_unassigned=True,
        include_restricted=getattr(args, "include_restricted", False),
        include_inactive=getattr(args, "include_inactive", False),
        limit=args.limit,
    )
    rows = search_store(delegated)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            project = f" project={row['project']}" if row["project"] else ""
            print(f"{row['id']}  {row['title']}  type={row['type']}{project}")
            if row["excerpt"]:
                print(f"  {row['excerpt']}")
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


def _write_versioned_bytes(preferred, data, write=True):
    preferred = Path(preferred)
    digest = hashlib.sha256(data).hexdigest()
    index = 0
    while True:
        if index == 0:
            target = preferred
        elif index == 1:
            target = preferred.with_name(f"{preferred.stem}-{digest[:12]}{preferred.suffix}")
        elif index == 2:
            target = preferred.with_name(f"{preferred.stem}-{digest}{preferred.suffix}")
        else:
            target = preferred.with_name(f"{preferred.stem}-{digest}-{index - 2}{preferred.suffix}")

        if not write:
            if not target.exists() and not target.is_symlink():
                return target, True
            if not target.is_symlink() and target.is_file() and _file_sha256(target) == digest:
                return target, False
            index += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with target.open("xb") as handle:
                created = True
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return target, True
        except FileExistsError:
            if not target.is_symlink() and target.is_file() and _file_sha256(target) == digest:
                return target, False
            index += 1
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise


def _manual_text(path):
    suffix = path.suffix.lower()
    if suffix not in MANUAL_SUFFIXES:
        raise ValueError("Manual import supports PDF/DOCX/RTF/HTML/TXT/Markdown/CSV/JSON.")
    if suffix in ARCHIVE_ONLY_SUFFIXES:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
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
    preferred = root / "imports/manual/raw" / relative_base.with_suffix(source.suffix.lower())
    raw_target, raw_created = _write_versioned_bytes(preferred, raw, write=not args.dry_run)
    raw_relative = raw_target.relative_to(root)
    raw_tail = raw_relative.relative_to("imports/manual/raw")
    text_relative = Path("imports/manual/text") / raw_tail.with_suffix(".md")
    written_paths = [raw_relative.as_posix()]
    title = source.stem or source.name
    markdown = None
    if text is not None:
        written_paths.append(text_relative.as_posix())
        markdown = "\n".join(
            [
                "---",
                f"title: {json.dumps(title, ensure_ascii=False)}",
                f"source_path: {json.dumps(raw_relative.as_posix())}",
                f"source_sha256: {json.dumps(digest)}",
                f"original_name: {json.dumps(source.name, ensure_ascii=False)}",
                f"media_type: {json.dumps(mimetypes.guess_type(source.name)[0] or 'application/octet-stream')}",
                'extractor: "utf-8"',
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
    text_exists = (root / text_relative).exists() if text is not None else True
    report = {
        "format_version": 1,
        "importer_version": IMPORTER_VERSION,
        "kind": "manual",
        "input_path": str(source),
        "input_sha256": digest,
        "imported_at": now.isoformat(),
        "new": 1 if raw_created or not text_exists else 0,
        "updated": 0,
        "duplicate": 1 if not raw_created and text_exists else 0,
        "failed": 0,
        "archived_without_text": 1 if text is None else 0,
        "written_paths": written_paths,
        "index_result": "not_run",
        "restore_suggestion": "Install or run a dedicated extractor, then import a text/Markdown sidecar." if text is None else "",
    }
    if not args.dry_run:
        if markdown is not None and not (root / text_relative).exists():
            (root / text_relative).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(root / text_relative, markdown)
        report["report_path"] = _write_import_report(root, "manual", digest, report)
    print(f"Raw: {raw_relative.as_posix()}")
    if text is None:
        print("Text: archived_without_text")
    else:
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
        content, message_count = _render_conversation(conversation)
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        preferred = output_root / relative
        preferred_exists = preferred.exists() or preferred.is_symlink()
        target, wrote = _write_versioned_bytes(preferred, encoded, write=not args.dry_run)
        relative = target.relative_to(output_root)
        if wrote and preferred_exists:
            updated += 1
        elif wrote:
            created += 1
        else:
            unchanged += 1
        records.append(
            {
                "conversation_id": conversation_id,
                "title": title,
                "path": (Path("imports/chatgpt/conversations") / relative).as_posix(),
                "sha256": digest,
                "message_count": message_count,
            }
        )

    raw_only = unchanged
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
        "restore_suggestion": "No recent restore is performed by import-chatgpt in v0.7.0.",
    }
    previous_records = []
    if manifest_path.is_file():
        try:
            previous_records = json.loads(manifest_path.read_text(encoding="utf-8")).get("conversations", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            previous_records = []
    merged_records = {
        (record.get("path"), record.get("sha256")): record
        for record in previous_records + records
        if isinstance(record, dict) and record.get("path") and record.get("sha256")
    }
    manifest = {
        "format_version": 1,
        "source": Path(args.zip).name,
        "conversations": sorted(merged_records.values(), key=lambda item: (item["conversation_id"], item["path"])),
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
    search_parser.add_argument("--include-restricted", action="store_true")
    search_parser.add_argument("--include-inactive", action="store_true")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    import_parser = subparsers.add_parser("import-chatgpt", help="Archive conversations from an official export ZIP.")
    import_parser.add_argument("--zip", required=True)
    import_parser.add_argument("--root", required=True)
    import_parser.add_argument("--dry-run", action="store_true")

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
