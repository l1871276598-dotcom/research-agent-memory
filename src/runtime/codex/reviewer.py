"""Read-only Codex reviewer for LAOS reflection.

Lifecycle behavior is adapted from NousResearch/hermes-agent app-server session
and event projector modules (MIT License).
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .app_server import CodexAppServerClient


_TOOL_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}


def _thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread") or {}
    value = thread.get("id") or thread.get("sessionId") or result.get("threadId")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("codex thread/start returned no thread id")
    return value


def _prompt(messages: list[dict[str, Any]]) -> str:
    return (
        "Review only the supplied conversation. Return the exact JSON object "
        "requested by the final message, with no Markdown wrapper. Do not call "
        "tools or use external context.\n\n"
        + json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    )


def _json_text(text: str) -> str:
    value = text.strip()
    if value.startswith("```json") and value.endswith("```"):
        value = value[7:-3].strip()
    elif value.startswith("```") and value.endswith("```"):
        value = value[3:-3].strip()
    json.loads(value)
    return value


class CodexConversationReviewer:
    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        codex_home: str | None = None,
        timeout: float = 120.0,
        client_factory: Callable[..., CodexAppServerClient] = CodexAppServerClient,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.codex_bin = codex_bin
        self.codex_home = codex_home
        self.timeout = float(timeout)
        self.client_factory = client_factory

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise ValueError("review messages must be a list of objects")

        with tempfile.TemporaryDirectory(prefix="laos-review-") as directory:
            client = self.client_factory(
                codex_bin=self.codex_bin,
                codex_home=self.codex_home,
            )
            try:
                client.initialize(
                    client_name="laos",
                    client_title="LAOS Conversation Review",
                    client_version="0.1",
                )
                started = client.request(
                    "thread/start",
                    {
                        "cwd": str(Path(directory)),
                        "ephemeral": True,
                        "approvalPolicy": "never",
                        "sandbox": "readOnly",
                    },
                    timeout=min(self.timeout, 15.0),
                )
                thread_id = _thread_id(started)
                turn = client.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": _prompt(messages)}],
                        "approvalPolicy": "never",
                        "sandboxPolicy": {"type": "readOnly"},
                    },
                    timeout=min(self.timeout, 15.0),
                )
                turn_id = (turn.get("turn") or {}).get("id")
                deadline = time.monotonic() + self.timeout
                final_text = ""
                completed = False

                while time.monotonic() < deadline and not completed:
                    request = client.take_server_request(timeout=0)
                    if request is not None:
                        client.respond(request.get("id"), {"decision": "decline"})
                        continue
                    note = client.take_notification(timeout=0.25)
                    if note is None:
                        if not client.is_alive():
                            raise RuntimeError("codex app-server exited during review")
                        continue
                    method = note.get("method", "")
                    params = note.get("params") or {}
                    if method == "item/completed":
                        item = params.get("item") or {}
                        item_type = item.get("type")
                        if item_type in _TOOL_ITEMS:
                            raise RuntimeError("restricted conversation review attempted a tool")
                        if item_type == "agentMessage":
                            final_text = str(item.get("text") or "")
                    elif method == "turn/completed":
                        completed = True
                        status = (params.get("turn") or {}).get("status")
                        if status not in {None, "completed"}:
                            raise RuntimeError(f"codex review ended with status {status}")

                if not completed:
                    if turn_id:
                        try:
                            client.request(
                                "turn/interrupt",
                                {"threadId": thread_id, "turnId": turn_id},
                                timeout=5.0,
                            )
                        except Exception:
                            pass
                    raise TimeoutError("codex conversation review timed out")
                if not final_text.strip():
                    raise RuntimeError("codex review returned no agent message")
                return _json_text(final_text)
            finally:
                client.close()
