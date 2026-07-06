#!/usr/bin/env python3

import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


MAX_INPUT = 512 * 1024
MAX_OPTION = 256
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SEARCH_SCAN = 10_000
CAPTURE_FIELDS = {
    "session_alias",
    "user_message",
    "assistant_response",
    "checkpoint_id",
    "conversation_summary",
    "assistant_response_complete",
    "source_conversation_id",
    "source_user_message_id",
    "source_assistant_message_id",
    "branch_id",
    "version",
    "captured_at",
    "force_review",
}
CAPTURE_REQUIRED = {"session_alias", "user_message", "assistant_response"}
SEARCH_FIELDS = {"query", "workspace", "project", "limit"}
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
CONFIDENTIALITIES = {"public", "personal", "internal", "restricted"}


class AdapterError(Exception):
    def __init__(self, code):
        self.code = code


def _fail(code):
    raise AdapterError(code)


def _directory(raw):
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        _fail("invalid_configuration")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        _fail("invalid_configuration")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("invalid_configuration")
    if raw != str(resolved):
        _fail("invalid_configuration")
    return resolved


def _overlap(left, right):
    return left == right or left in right.parents or right in left.parents


def _option(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.strip() or len(value) > MAX_OPTION or "\x00" in value:
        _fail("invalid_configuration")
    return value


def _configuration(code_root):
    data = _directory(os.environ.get("LAOS_DATA_ROOT"))
    state = _directory(os.environ.get("LAOS_STATE_DIR"))
    workspace = _option("LAOS_CHECKPOINT_WORKSPACE")
    if workspace not in {"personal", "work"}:
        _fail("invalid_configuration")

    marker = data / ".research-agent-root"
    if marker.is_symlink() or not marker.is_file():
        _fail("invalid_configuration")
    try:
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        _fail("invalid_configuration")
    if marker_data != {
        "type": "research-agent-data-root",
        "format_version": 1,
    }:
        _fail("invalid_configuration")

    if any(_overlap(left, right) for left, right in ((data, state), (data, code_root), (state, code_root))):
        _fail("invalid_configuration")
    temp_roots = [Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"), Path("/usr/tmp")]
    if os.name == "nt":
        temp_roots.extend((Path("C:/Windows/Temp"), Path("C:/Temp")))
    for root in temp_roots:
        try:
            if root.is_dir() and _overlap(root.resolve(strict=True), state):
                _fail("invalid_configuration")
        except OSError:
            continue
    if {"Mobile Documents", "CloudStorage"} & set(state.parts):
        _fail("invalid_configuration")

    confidentiality = _option("LAOS_CHECKPOINT_CONFIDENTIALITY", "personal")
    if confidentiality not in CONFIDENTIALITIES:
        _fail("invalid_configuration")
    return {
        "data": data,
        "state": state,
        "workspace": workspace,
        "project": _option("LAOS_CHECKPOINT_PROJECT"),
        "account_id": _option("LAOS_CHECKPOINT_ACCOUNT_ID", "default"),
        "profile": _option("LAOS_CHECKPOINT_PROFILE"),
        "confidentiality": confidentiality,
    }


def _request():
    if len(sys.argv) != 1:
        _fail("invalid_request")
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        _fail("invalid_request")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        _fail("invalid_request")
    if not isinstance(value, dict) or set(value) != {"operation", "arguments"}:
        _fail("invalid_request")
    if not isinstance(value["operation"], str) or value["operation"] not in {
        "capture_checkpoint",
        "session_search",
        "session_get",
    }:
        _fail("invalid_request")
    if not isinstance(value["arguments"], dict):
        _fail("invalid_request")
    return value["operation"], value["arguments"]


def _facade(configuration, serve_laos):
    argv = [
        f'--data-root={configuration["data"]}',
        f'--state-dir={configuration["state"]}',
        "--enable-checkpoint-capture",
        f'--checkpoint-workspace={configuration["workspace"]}',
        f'--checkpoint-account-id={configuration["account_id"]}',
        f'--checkpoint-confidentiality={configuration["confidentiality"]}',
    ]
    for flag, key in (("--checkpoint-project", "project"), ("--checkpoint-profile", "profile")):
        if configuration[key] is not None:
            argv.append(f"{flag}={configuration[key]}")
    return serve_laos.build_facade(serve_laos._parser().parse_args(argv))


def _dispatch(operation, arguments, configuration, facade):
    if operation == "capture_checkpoint":
        if not CAPTURE_REQUIRED <= set(arguments) or not set(arguments) <= CAPTURE_FIELDS:
            _fail("invalid_request")
        try:
            return facade.capture_checkpoint(**arguments)
        except (TypeError, ValueError):
            _fail("invalid_request")

    if operation == "session_search":
        if set(arguments) - SEARCH_FIELDS or "query" not in arguments:
            _fail("invalid_request")
        workspace = arguments.get("workspace", configuration["workspace"])
        project = arguments.get("project", configuration["project"])
        if workspace != configuration["workspace"] or project != configuration["project"]:
            _fail("scope_violation")
        limit = arguments.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            _fail("invalid_request")
        try:
            search_limit = limit if project is not None else MAX_SEARCH_SCAN
            rows = facade.session_search(arguments["query"], workspace, project, search_limit)
            if project is not None:
                return rows
            projects = {}
            scoped = []
            for row in rows:
                session_id = row["session_id"]
                if session_id not in projects:
                    projects[session_id] = facade.sessions.get(
                        session_id,
                        include_messages=False,
                    ).get("project")
                if projects[session_id] is None:
                    scoped.append(row)
                    if len(scoped) == limit:
                        break
            if len(scoped) < limit and len(rows) == MAX_SEARCH_SCAN:
                _fail("request_failed")
            return scoped
        except (TypeError, ValueError):
            _fail("invalid_request")

    if set(arguments) != {"session_id"} or not isinstance(arguments["session_id"], str):
        _fail("invalid_request")
    if SESSION_ID.fullmatch(arguments["session_id"]) is None:
        _fail("invalid_request")
    try:
        result = facade.session_get(arguments["session_id"])
    except ValueError:
        _fail("session_not_found")
    if result.get("workspace") != configuration["workspace"] or result.get("project") != configuration["project"]:
        _fail("scope_violation")
    return result


def run():
    adapter = Path(__file__).resolve(strict=True)
    code_root = adapter.parents[1]
    src = code_root / "src"
    sys.path.insert(0, str(code_root))
    sys.path.insert(0, str(src))
    from tools import serve_laos

    configuration = _configuration(code_root)
    operation, arguments = _request()
    return _dispatch(operation, arguments, configuration, _facade(configuration, serve_laos))


def _encode(response):
    return (
        json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def main():
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(
            sink
        ), redirect_stderr(sink):
            result = run()
        response = {"ok": True, "result": result}
        payload = _encode(response)
        if len(payload) > MAX_RESPONSE_BYTES:
            _fail("request_failed")
        status = 0
    except AdapterError as exc:
        response = {"ok": False, "error": {"code": exc.code}}
        status = 1
        payload = _encode(response)
    except (Exception, SystemExit):
        response = {"ok": False, "error": {"code": "request_failed"}}
        status = 1
        payload = b'{"ok":false,"error":{"code":"request_failed"}}\n'
    sys.stdout.buffer.write(payload)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
