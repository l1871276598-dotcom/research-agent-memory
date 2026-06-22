import argparse
import hashlib
import hmac
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
LIVE_ROLES = {"user": "User", "assistant": "Assistant"}


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


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _live_time(value, default):
    if not value:
        return default.isoformat()
    if not isinstance(value, str):
        raise ValueError("Live archive timestamps must be strings.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Live archive timestamps must use ISO 8601.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _live_messages(value):
    if not isinstance(value, list) or not value:
        raise ValueError("messages must be a non-empty list.")
    messages = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Each message must be an object.")
        role = str(item.get("role") or "").lower()
        if role not in LIVE_ROLES:
            raise ValueError("Only visible user and assistant messages may be archived.")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Each message content must be a non-empty string.")
        message = {"role": role, "content": content.strip()}
        if item.get("created_at"):
            message["created_at"] = _live_time(item["created_at"], _utc_now())
        messages.append(message)
    return messages


def _live_key(payload, messages):
    supplied = payload.get("conversation_key")
    if supplied:
        if not isinstance(supplied, str) or len(supplied) > 128:
            raise ValueError("conversation_key must be a string up to 128 characters.")
        return safe_slug(supplied)[:128]
    seed = "\n".join(f"{item['role']}:{item['content']}" for item in messages[:2])
    return "live-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _existing_live_hash(path):
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith("content_sha256: "):
            try:
                value = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, str) else None
    return None


def archive_live(root, payload):
    root = _require_data_root(root)
    if not isinstance(payload, dict):
        raise ValueError("Archive payload must be a JSON object.")
    title = payload.get("title") or "Untitled conversation"
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError("title must be a non-empty string up to 500 characters.")
    title = title.strip()
    messages = _live_messages(payload.get("messages"))
    now = _utc_now()
    created = _live_time(payload.get("created_at"), now)
    updated = _live_time(payload.get("updated_at"), now)
    summary = payload.get("summary")
    if summary is not None and (not isinstance(summary, str) or len(summary) > 20000):
        raise ValueError("summary must be a string up to 20000 characters.")
    complete = bool(payload.get("is_complete", False))
    archive_id = _live_key(payload, messages)
    semantic = {
        "archive_id": archive_id,
        "title": title,
        "created": _live_time(payload.get("created_at"), now) if payload.get("created_at") else "",
        "updated": _live_time(payload.get("updated_at"), now) if payload.get("updated_at") else "",
        "summary": (summary or "").strip(),
        "is_complete": complete,
        "messages": messages,
    }
    content_hash = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    output_root = root / "imports" / "chatgpt" / "live"
    existing = sorted(output_root.rglob(f"{archive_id}.md")) if output_root.exists() else []
    target = existing[0] if existing else output_root / f"{now.year:04d}" / f"{now.month:02d}" / f"{archive_id}.md"
    if _existing_live_hash(target) == content_hash:
        status = "unchanged"
    else:
        status = "updated" if target.exists() else "created"
        lines = [
            "---",
            f"archive_id: {json.dumps(archive_id, ensure_ascii=False)}",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"created: {json.dumps(created, ensure_ascii=False)}",
            f"updated: {json.dumps(updated, ensure_ascii=False)}",
            f"archived_at: {json.dumps(now.isoformat(), ensure_ascii=False)}",
            'source: "chatgpt_action"',
            'coverage: "current_model_context"',
            f"is_complete: {'true' if complete else 'false'}",
            f"message_count: {len(messages)}",
            f"content_sha256: {json.dumps(content_hash)}",
            "---",
            "",
            f"# {title}",
            "",
        ]
        if semantic["summary"]:
            lines.extend(["## Summary", "", semantic["summary"], ""])
        for message in messages:
            lines.extend([f"## {LIVE_ROLES[message['role']]}", ""])
            if message.get("created_at"):
                lines.extend([f"_Time: {message['created_at']}_", ""])
            lines.extend([message["content"], ""])
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, "\n".join(lines).rstrip() + "\n")

    return {
        "status": status,
        "archive_id": archive_id,
        "relative_path": target.relative_to(root).as_posix(),
        "message_count": len(messages),
        "content_sha256": content_hash,
        "is_complete": complete,
    }


def _read_token(path):
    token = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if len(token) < 32 or any(char.isspace() for char in token):
        raise ValueError("Archive token must contain at least 32 non-whitespace characters.")
    return token


def _archive_server(root, token, host, port, max_bytes):
    expected = f"Bearer {token}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "ResearchAgentArchive/1"

        def _send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/archive":
                self._send(404, {"error": "not_found"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if length < 1 or length > max_bytes:
                self._send(413, {"error": "invalid_content_length"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = archive_live(root, payload)
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(201 if result["status"] == "created" else 200, result)

        def log_message(self, format, *args):
            print(f"{self.client_address[0]} - {format % args}", file=sys.stderr)

    return ThreadingHTTPServer((host, port), Handler)


def serve_chatgpt(args):
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("The archive service must bind to loopback; expose it through a secure HTTPS tunnel.")
    root = _require_data_root(args.root)
    token = _read_token(args.token_file)
    if not (1 <= args.port <= 65535) or args.max_bytes < 1024:
        raise ValueError("Invalid server port or max-bytes value.")
    server = _archive_server(root, token, args.host, args.port, args.max_bytes)
    print(f"Archive service: http://{args.host}:{server.server_port}")
    print("Endpoints: GET /health, POST /archive")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser():
    parser = argparse.ArgumentParser(description="Search memory and archive ChatGPT conversations.")
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

    serve_parser = subparsers.add_parser("serve-chatgpt", help="Receive live ChatGPT archive actions.")
    serve_parser.add_argument("--root", required=True)
    serve_parser.add_argument("--token-file", required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--max-bytes", type=int, default=1_000_000)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "search":
            search(args)
        elif args.command == "import-chatgpt":
            import_chatgpt(args)
        else:
            serve_chatgpt(args)
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
