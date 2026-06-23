import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import memory


def _safe_id(value):
    text = str(value or "conversation")
    text = re.sub(r"[^A-Za-z0-9._:-]+", "-", text).strip("-")
    return text or "conversation"


def _parse_date(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def _message_role(message):
    author = message.get("author") or message.get("role") or "message"
    if isinstance(author, dict):
        author = author.get("role") or author.get("name") or "message"
    return str(author or "message").capitalize()


def _message_text(message):
    content = message.get("content")
    if isinstance(content, dict):
        parts = content.get("parts") or []
        values = []
        for part in parts:
            if isinstance(part, str):
                values.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    values.append(text)
        if values:
            return "\n\n".join(value.strip() for value in values if value.strip())
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    text = message.get("text") if content is None else content
    if isinstance(text, dict):
        return json.dumps(text, ensure_ascii=False, sort_keys=True)
    return str(text or "")


def _active_mapping_messages(conversation):
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []
    node_id = conversation.get("current_node")
    chain = []
    seen = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            break
        message = node.get("message")
        if isinstance(message, dict):
            chain.append(message)
        node_id = node.get("parent")
    if chain:
        return list(reversed(chain))
    return [node.get("message") for node in mapping.values() if isinstance(node, dict) and isinstance(node.get("message"), dict)]


def _conversation_text(conversation):
    if isinstance(conversation.get("messages"), list):
        lines = []
        for message in conversation["messages"]:
            if isinstance(message, dict):
                lines.append(f"## {_message_role(message)}\n\n{_message_text(message)}".strip())
        return "\n\n".join(lines).strip()
    messages = _active_mapping_messages(conversation)
    if messages:
        return "\n\n".join(f"## {_message_role(message)}\n\n{_message_text(message)}".strip() for message in messages).strip()
    return json.dumps(conversation, ensure_ascii=False, indent=2, sort_keys=True)


def _raw_markdown(conversation):
    title = conversation.get("title") or "Untitled ChatGPT conversation"
    conv_id = _safe_id(conversation.get("id") or title)
    updated = _parse_date(conversation.get("update_time") or conversation.get("updated"))
    body = _conversation_text(conversation)
    return (
        f"---\n"
        f'source: "chatgpt_export"\n'
        f'source_id: "{conv_id}"\n'
        f'title: {json.dumps(title, ensure_ascii=False)}\n'
        f'updated: "{updated.isoformat()}"\n'
        f"---\n\n"
        f"# {title}\n\n{body}\n"
    )


def _raw_path(root, conversation):
    conv_id = _safe_id(conversation.get("id") or conversation.get("title"))
    updated = _parse_date(conversation.get("update_time") or conversation.get("updated"))
    return root / "imports" / "chatgpt" / "conversations" / f"{updated:%Y}" / f"{updated:%m}" / f"{conv_id}.md"


def _recent_record(root, raw_path, conversation, raw_sha):
    title = conversation.get("title") or "Untitled ChatGPT conversation"
    conv_id = _safe_id(conversation.get("id") or title)
    created = _parse_date(conversation.get("create_time") or conversation.get("created"))
    updated = _parse_date(conversation.get("update_time") or conversation.get("updated"))
    return {
        "schema": "recent/v1",
        "id": f"recent-chatgpt-{conv_id}",
        "type": "session",
        "tier": "recent",
        "title": title,
        "created": created.isoformat(),
        "updated": updated.isoformat(),
        "status": "active",
        "source": "chatgpt_export",
        "source_id": conv_id,
        "source_path": raw_path.relative_to(root).as_posix(),
        "source_sha256": raw_sha,
        "retention_until": (updated + timedelta(days=memory.RECENT_BODY_DAYS)).isoformat(),
        "distillation_status": "pending",
        "protected": "false",
        "content": _conversation_text(conversation),
    }


def _render_recent(record):
    return memory.render_front_matter(record) + "\n\n" + record["content"].rstrip() + "\n"


def _load_conversations(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if Path(name).name == "conversations.json"]
            if not names:
                raise KeyError("conversations.json")
            data = json.loads(archive.read(names[0]).decode("utf-8"))
    except KeyError as exc:
        raise ValueError("conversations.json was not found in the export ZIP.") from exc
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError("Invalid ChatGPT export ZIP.") from exc
    if not isinstance(data, list):
        raise ValueError("ChatGPT conversations.json must be a list.")
    return data


def import_zip(args):
    root = memory._require_data_root(args.root)
    state_dir = Path(args.state_dir) if args.state_dir else memory.default_state_dir()
    conversations = _load_conversations(args.zip)
    summary = {"imported": 0, "updated": 0, "unchanged": 0, "dry_run": args.dry_run}
    with memory.write_lock(root, state_dir):
        for conversation in conversations:
            raw_path = _raw_path(root, conversation)
            raw_text = _raw_markdown(conversation)
            raw_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            recent_record = _recent_record(root, raw_path, conversation, raw_sha)
            recent_path = root / "memory" / "recent" / f"{recent_record['id']}.md"
            existing_sha = None
            if recent_path.exists():
                existing, _body, errors = memory.parse_memory_document(recent_path)
                if not errors and existing:
                    existing_sha = existing.get("source_sha256")
            if existing_sha == raw_sha:
                summary["unchanged"] += 1
                continue
            if recent_path.exists():
                summary["updated"] += 1
            else:
                summary["imported"] += 1
            if args.dry_run:
                continue
            memory.atomic_write_text(raw_path, raw_text)
            memory.atomic_write_text(recent_path, _render_recent(recent_record))
        if not args.dry_run and (summary["imported"] or summary["updated"]):
            memory.index_store(type("Args", (), {"root": str(root), "state_dir": str(state_dir), "dry_run": False})())
    return summary


def build_parser():
    parser = argparse.ArgumentParser(description="Import ChatGPT export ZIPs into recent memory.")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-zip")
    imp.add_argument("--root", required=True)
    imp.add_argument("--state-dir", required=True)
    imp.add_argument("--zip", required=True)
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "import-zip":
            summary = import_zip(args)
        else:
            parser.error("unknown command")
            return 2
        if args.json_output:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            for key, value in summary.items():
                print(f"{key}: {value}")
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
