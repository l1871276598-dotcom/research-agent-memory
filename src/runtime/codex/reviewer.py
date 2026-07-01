"""Read-only Codex execution for LAOS review and bounded tasks.

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
_REVIEW_INSTRUCTION = (
    "Review only the supplied conversation. Return the exact JSON object "
    "requested by the final message, with no Markdown wrapper. Do not call "
    "tools or use external context."
)
_TASK_INSTRUCTION = (
    "Execute only the supplied bounded task using the supplied messages and "
    "evidence. Return the final text response. Do not call tools, inspect other "
    "files, use external context, or modify anything."
)


def _thread_id(result: dict[str, Any]) -> str:
    thread = result.get("thread") or {}
    value = thread.get("id") or thread.get("sessionId") or result.get("threadId")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("codex thread/start returned no thread id")
    return value


def _prompt(messages: list[dict[str, Any]], instruction: str) -> str:
    return instruction + "\n\n" + json.dumps(
        messages,
        ensure_ascii=False,
        separators=(",", ":"),
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
        json_only: bool = True,
        instruction: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not isinstance(json_only, bool):
            raise ValueError("json_only must be boolean")
        if instruction is not None and (
            not isinstance(instruction, str) or not instruction.strip()
        ):
            raise ValueError("instruction must be a non-empty string")
        self.codex_bin = codex_bin
        self.codex_home = codex_home
        self.timeout = float(timeout)
        self.client_factory = client_factory
        self.json_only = json_only
        self.instruction = instruction or (
            _REVIEW_INSTRUCTION if json_only else _TASK_INSTRUCTION
        )

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        if not isinstance(messages, list) or not all(
            isinstance(item, dict) for item in messages
        ):
            raise ValueError("review messages must be a list of objects")

        with tempfile.TemporaryDirectory(prefix="laos-codex-readonly-") as directory:
            client = self.client_factory(
                codex_bin=self.codex_bin,
                codex_home=self.codex_home,
            )
            try:
                client.initialize(
                    client_name="laos",
                    client_title="LAOS Read-Only Execution",
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
                        "input": [
                            {
                                "type": "text",
                                "text": _prompt(messages, self.instruction),
                            }
                        ],
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
                            raise RuntimeError("codex app-server exited during execution")
                        continue
                    method = note.get("method", "")
                    params = note.get("params") or {}
                    if method == "item/completed":
                        item = params.get("item") or {}
                        item_type = item.get("type")
                        if item_type in _TOOL_ITEMS:
                            raise RuntimeError("read-only Codex execution attempted a tool")
                        if item_type == "agentMessage":
                            final_text = str(item.get("text") or "")
                    elif method == "turn/completed":
                        completed = True
                        status = (params.get("turn") or {}).get("status")
                        if status not in {None, "completed"}:
                            raise RuntimeError(
                                f"codex execution ended with status {status}"
                            )

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
                    raise TimeoutError("codex read-only execution timed out")
                if not final_text.strip():
                    raise RuntimeError("codex execution returned no agent message")
                return _json_text(final_text) if self.json_only else final_text.strip()
            finally:
                client.close()
