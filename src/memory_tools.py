import argparse
import hashlib
import html
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import chatgpt_export_sync
import memory


TEXT_EXTENSIONS = {".md", ".txt", ".json", ".csv"}
TEMP_SUFFIXES = {".tmp", ".part", ".download", ".crdownload"}


def _query_tokens(text):
    return memory.extract_search_terms(text)


def _fts_query(text):
    return memory.build_fts_query(text)


def search(args):
    compat = argparse.Namespace(
        query=args.query,
        root=args.root,
        state_dir=args.state_dir,
        workspace=args.workspace or "personal",
        type=args.type,
        project=args.project,
        context_id=getattr(args, "context_id", None),
        status=[args.status] if getattr(args, "status", None) else None,
        include_historical=False,
        include_restricted=False,
        limit=args.limit,
    )
    summary = memory.search_store(compat)
    if getattr(args, "json", False):
        print(json.dumps(summary["results"], ensure_ascii=False, indent=2))
    else:
        for row in summary["results"]:
            project = f" project={row['project']}" if row["project"] else ""
            print(f"{row['id']}  {row['title']}  type={row['type']}{project}")
            if row["excerpt"]:
                print(f"  {row['excerpt']}")
            print(f"  {row['relative_path']}")
        print(f"Results: {summary['count']}")
    return summary["count"]


def _load_conversations(zip_path):
    return chatgpt_export_sync._load_conversations(zip_path)


def import_chatgpt(args):
    state_dir = getattr(args, "state_dir", None) or memory.default_state_dir()
    summary = chatgpt_export_sync.import_zip(
        argparse.Namespace(root=args.root, state_dir=state_dir, zip=args.zip, dry_run=args.dry_run)
    )
    print(f"Conversations: {summary['imported'] + summary['updated'] + summary['unchanged']}")
    print(f"Created: {summary['imported']}")
    print(f"Updated: {summary['updated']}")
    print(f"Unchanged: {summary['unchanged']}")
    if args.dry_run:
        print("Dry run: no files changed")
    else:
        print("Output: imports/chatgpt/conversations/YYYY/MM/*.md")
        print("Recent: memory/recent/recent-chatgpt-*.md")
    return summary


def _safe_name(name):
    return memory.safe_slug(Path(name).name).strip("-") or "document"


def _read_manifest(path):
    if not path.exists():
        return {"format_version": 1, "imports": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"format_version": 1, "imports": []}
    if not isinstance(data.get("imports"), list):
        data["imports"] = []
    return data


def _write_manifest(path, data, dry_run):
    if dry_run:
        return
    memory.atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _is_candidate(path):
    if not path.is_file() or path.is_symlink():
        return False
    if path.name.startswith(".") or path.name.endswith("~") or path.suffix.lower() in TEMP_SUFFIXES:
        return False
    return True


def _is_stable(path):
    try:
        first = path.stat()
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime_ns == second.st_mtime_ns


def _html_text(raw):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _docx_text(path):
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, OSError, zipfile.BadZipFile):
        raise ValueError("cannot extract docx text")
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml)
    return html.unescape("".join(parts)).strip()


def _external_text(command):
    if shutil.which(command[0]) is None:
        return None
    result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "text extraction failed")
    return result.stdout.strip()


def extract_text(path):
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8"), suffix.lstrip(".")
    if suffix == ".html":
        return _html_text(path.read_text(encoding="utf-8")), "html"
    if suffix == ".docx":
        return _docx_text(path), "docx"
    if suffix == ".rtf":
        text = _external_text(["textutil", "-convert", "txt", "-stdout", str(path)])
        return (text, "textutil") if text is not None else (None, None)
    if suffix == ".pdf":
        text = _external_text(["pdftotext", str(path), "-"])
        return (text, "pdftotext") if text is not None else (None, None)
    return None, None


def _render_recent(record, body):
    return memory.render_front_matter(record) + "\n\n" + body.rstrip() + "\n"


def _archive_path(root, sha256, source):
    today = date.today()
    name = f"{sha256[:12]}-{_safe_name(source.name)}"
    suffix = source.suffix.lower()
    if suffix and not name.endswith(suffix):
        name += suffix
    return root / "imports" / "manual" / "raw" / f"{today:%Y}" / f"{today:%m}" / name


def _recent_record(root, raw_path, source, sha256, text, extractor):
    today = date.today()
    recent_id = f"recent-manual-{sha256[:16]}"
    return {
        "schema": "recent/v1",
        "id": recent_id,
        "type": "session",
        "tier": "recent",
        "title": source.stem or source.name,
        "created": today.isoformat(),
        "updated": today.isoformat(),
        "status": "active",
        "source": "manual_import",
        "source_id": sha256[:16],
        "source_path": raw_path.relative_to(root).as_posix(),
        "source_sha256": sha256,
        "retention_until": (today + timedelta(days=memory.RECENT_BODY_DAYS)).isoformat(),
        "distillation_status": "pending",
        "protected": "false",
        "original_name": source.name,
        "media_type": source.suffix.lower().lstrip(".") or "unknown",
        "extractor": extractor,
        "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "content": text.strip(),
    }


def _process_file(root, state_dir, path, manifest, dry_run):
    if not _is_candidate(path):
        return "skipped"
    if not _is_stable(path):
        return "skipped"
    try:
        raw = path.read_bytes()
    except OSError:
        if not dry_run:
            quarantine = root / "imports" / "manual" / "quarantine" / path.name
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, quarantine)
        return "quarantined"
    sha256 = hashlib.sha256(raw).hexdigest()
    if any(row.get("sha256") == sha256 for row in manifest["imports"]):
        return "duplicate"
    raw_path = _archive_path(root, sha256, path)
    text = None
    extractor = None
    try:
        text, extractor = extract_text(path)
    except Exception:
        if not dry_run:
            quarantine = root / "imports" / "manual" / "quarantine" / path.name
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, quarantine)
        return "quarantined"
    status = "imported" if text else "archived_without_text"
    if not dry_run:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            tmp = raw_path.parent / f".tmp-{raw_path.name}"
            tmp.write_bytes(raw)
            tmp.replace(raw_path)
        if text:
            record = _recent_record(root, raw_path, path, sha256, text, extractor)
            recent_path = root / "memory" / "recent" / f"{record['id']}.md"
            if not recent_path.exists():
                memory.atomic_write_text(recent_path, _render_recent(record, text))
        manifest["imports"].append(
            {
                "sha256": sha256,
                "original_name": path.name,
                "raw_path": raw_path.relative_to(root).as_posix(),
                "status": status,
                "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
        )
    return status


def import_manual(args):
    root = memory._require_data_root(args.root)
    state_dir = Path(args.state_dir) if args.state_dir else memory.default_state_dir()
    manifest_path = root / "imports" / "manual" / "import_manifest.json"
    manifest = _read_manifest(manifest_path)
    candidates = []
    if args.scan_inbox:
        candidates.extend(sorted((root / "imports" / "manual" / "inbox").iterdir()))
    if args.file:
        candidates.append(Path(args.file).expanduser())
    summary = {
        "imported": 0,
        "duplicates": 0,
        "archived_without_text": 0,
        "quarantined": 0,
        "skipped": 0,
        "dry_run": args.dry_run,
    }
    with memory.write_lock(root, state_dir):
        for path in candidates:
            status = _process_file(root, state_dir, path, manifest, args.dry_run)
            key = {
                "imported": "imported",
                "duplicate": "duplicates",
                "archived_without_text": "archived_without_text",
                "quarantined": "quarantined",
                "skipped": "skipped",
            }[status]
            summary[key] += 1
        _write_manifest(manifest_path, manifest, args.dry_run)
        if not args.dry_run and summary["imported"]:
            memory.index_store(type("Args", (), {"root": str(root), "state_dir": str(state_dir), "dry_run": False})())
    return summary


def _db(root, state_dir):
    _, db = memory.check_state_dir(memory._require_data_root(root), state_dir or memory.default_state_dir())
    if not db.exists():
        raise ValueError("Database is not initialized. Run db-init first.")
    return db


def _access_filter(args, table="memories"):
    workspace = getattr(args, "workspace", None) or "personal"
    access_args = argparse.Namespace(workspace=workspace, include_restricted=False)
    confidentialities = memory._allowed_confidentiality(access_args)
    where = [
        f"{table}.workspace = ?",
        f"{table}.status = 'active'",
        f"{table}.confidentiality IN (" + ", ".join("?" for _ in confidentialities) + ")",
    ]
    return " AND ".join(where), [workspace, *confidentialities]


def _json_list(value):
    try:
        data = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _memory_row(row):
    keys = ["id", "title", "type", "tier", "status", "workspace", "confidentiality", "relative_path", "aliases"]
    data = dict(zip(keys, row))
    data["aliases"] = _json_list(data["aliases"])
    return data


def recall(args):
    root = memory._require_data_root(args.root)
    db = _db(root, args.state_dir)
    query = args.query
    with sqlite3.connect(db) as conn:
        access_sql, access_params = _access_filter(args)
        rows = conn.execute(
            f"""
            SELECT id, title, type, tier, status, workspace, confidentiality, relative_path, aliases
            FROM memories
            WHERE {access_sql}
            """,
            access_params,
        ).fetchall()
        memories = [_memory_row(row) for row in rows]
        exact = [
            row
            for row in memories
            if row["id"] == query or row["title"] == query or query in row["aliases"]
        ]
        primary_ids = {row["id"] for row in exact}
        related = []
        if primary_ids:
            marks = ", ".join("?" for _ in primary_ids)
            link_access_sql, link_access_params = _link_access_filter(args)
            related = [
                dict(zip(["source_id", "target_id", "target_ref", "relation", "resolved"], row))
                for row in conn.execute(
                    f"""
                    SELECT links.source_id, links.target_id, links.target_ref, links.relation, links.resolved
                    FROM links
                    WHERE ({link_access_sql})
                      AND (links.source_id IN ({marks}) OR links.target_id IN ({marks}))
                    """,
                    tuple(link_access_params) + tuple(primary_ids) + tuple(primary_ids),
                )
            ]
        recent = [row for row in memories if row["tier"] == "recent" and query in (row["title"] + " " + " ".join(row["aliases"]))]
    return {"primary": exact, "related": related, "recent_evidence": recent, "unresolved": []}


def _link_access_filter(args):
    source_sql, source_params = _access_filter(args, "source")
    target_sql, target_params = _access_filter(args, "target")
    return (
        "links.source_id IN (SELECT source.id FROM memories source WHERE "
        + source_sql
        + ") AND (links.target_id IS NULL OR links.target_id IN (SELECT target.id FROM memories target WHERE "
        + target_sql
        + "))"
    ), source_params + target_params


def link_query(args, mode):
    db = _db(args.root, args.state_dir)
    with sqlite3.connect(db) as conn:
        access_sql, access_params = _link_access_filter(args)
        if mode == "outgoing":
            rows = conn.execute(
                "SELECT " + ", ".join(memory.LINK_COLUMNS) + f" FROM links WHERE {access_sql} AND source_id = ? ORDER BY target_ref",
                tuple(access_params) + (args.target_id,),
            ).fetchall()
        elif mode == "backlinks":
            rows = conn.execute(
                "SELECT " + ", ".join(memory.LINK_COLUMNS) + f" FROM links WHERE {access_sql} AND (target_id = ? OR target_ref = ?) ORDER BY source_id",
                tuple(access_params) + (args.target_id, args.target_id),
            ).fetchall()
        elif mode == "unresolved":
            rows = conn.execute(
                "SELECT " + ", ".join(memory.LINK_COLUMNS) + f" FROM links WHERE {access_sql} AND resolved = 0 ORDER BY source_id, target_ref",
                access_params,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT " + ", ".join(memory.LINK_COLUMNS) + f" FROM links WHERE {access_sql} ORDER BY source_id, target_ref",
                access_params,
            ).fetchall()
    links = [dict(zip(memory.LINK_COLUMNS, row)) for row in rows]
    key = "unresolved" if mode == "unresolved" else "links"
    return {key: links, "count": len(links)}


def launchd_plist(args):
    root = memory._require_data_root(args.root)
    program = Path(__file__).resolve()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{html.escape(args.label)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{html.escape(sys.executable)}</string>
    <string>{html.escape(str(program))}</string>
    <string>import-manual</string>
    <string>--root</string>
    <string>{html.escape(str(root))}</string>
    <string>--state-dir</string>
    <string>{html.escape(str(Path(args.state_dir).expanduser()))}</string>
    <string>--scan-inbox</string>
  </array>
  <key>WatchPaths</key>
  <array>
    <string>{html.escape(str(root / "imports" / "manual" / "inbox"))}</string>
  </array>
  <key>StartInterval</key>
  <integer>{args.interval}</integer>
  <key>StandardOutPath</key>
  <string>{html.escape(str(Path(args.state_dir).expanduser() / "manual-import.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{html.escape(str(Path(args.state_dir).expanduser() / "manual-import.err.log"))}</string>
</dict>
</plist>
"""
    return {"plist": plist}


def build_parser():
    parser = argparse.ArgumentParser(description="Research memory helper tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    search_parser = sub.add_parser("search", help="Search the local SQLite FTS5 index.")
    search_parser.add_argument("query")
    search_parser.add_argument("--root", required=True)
    search_parser.add_argument("--state-dir", default=str(memory.default_state_dir()))
    search_parser.add_argument("--type")
    search_parser.add_argument("--project")
    search_parser.add_argument("--workspace")
    search_parser.add_argument("--status")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    import_parser = sub.add_parser("import-chatgpt", help="Import an official ChatGPT export ZIP.")
    import_parser.add_argument("--zip", required=True)
    import_parser.add_argument("--root", required=True)
    import_parser.add_argument("--state-dir", default=str(memory.default_state_dir()))
    import_parser.add_argument("--dry-run", action="store_true")
    import_parser.add_argument("--json", action="store_true", dest="json_output")

    imp = sub.add_parser("import-manual")
    imp.add_argument("--root", required=True)
    imp.add_argument("--state-dir", required=True)
    imp.add_argument("--scan-inbox", action="store_true")
    imp.add_argument("--file")
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--json", action="store_true", dest="json_output")

    rec = sub.add_parser("recall")
    rec.add_argument("query")
    rec.add_argument("--root", required=True)
    rec.add_argument("--state-dir", required=True)
    rec.add_argument("--workspace", default="personal", choices=memory.WORKSPACE_CHOICES)
    rec.add_argument("--json", action="store_true", dest="json_output")

    for name in ["backlinks", "outgoing", "related"]:
        item = sub.add_parser(name)
        item.add_argument("target_id")
        item.add_argument("--root", required=True)
        item.add_argument("--state-dir", required=True)
        item.add_argument("--workspace", default="personal", choices=memory.WORKSPACE_CHOICES)
        item.add_argument("--depth", type=int, default=1)
        item.add_argument("--json", action="store_true", dest="json_output")

    unresolved = sub.add_parser("unresolved")
    unresolved.add_argument("--root", required=True)
    unresolved.add_argument("--state-dir", required=True)
    unresolved.add_argument("--workspace", default="personal", choices=memory.WORKSPACE_CHOICES)
    unresolved.add_argument("--json", action="store_true", dest="json_output")

    check = sub.add_parser("check-links")
    check.add_argument("--root", required=True)
    check.add_argument("--state-dir", required=True)
    check.add_argument("--workspace", default="personal", choices=memory.WORKSPACE_CHOICES)
    check.add_argument("--json", action="store_true", dest="json_output")

    launchd = sub.add_parser("launchd-plist")
    launchd.add_argument("--root", required=True)
    launchd.add_argument("--state-dir", required=True)
    launchd.add_argument("--label", default="local.research-agent-memory.manual-import")
    launchd.add_argument("--interval", type=int, default=300)
    launchd.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            search(args)
            return 0
        if args.command == "import-chatgpt":
            summary = import_chatgpt(args)
            if getattr(args, "json_output", False):
                print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "import-manual":
            summary = import_manual(args)
        elif args.command == "recall":
            summary = recall(args)
        elif args.command in {"backlinks", "outgoing", "related", "unresolved", "check-links"}:
            summary = link_query(args, args.command)
        elif args.command == "launchd-plist":
            summary = launchd_plist(args)
        else:
            parser.error("unknown command")
            return 2
        if getattr(args, "json_output", False):
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "launchd-plist":
            print(summary["plist"], end="")
        else:
            for key, value in summary.items():
                print(f"{key}: {value}")
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
