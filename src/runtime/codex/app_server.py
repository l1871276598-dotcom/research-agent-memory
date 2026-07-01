"""Codex app-server JSON-RPC client for LAOS.

Adapted from NousResearch/hermes-agent
`agent/transports/codex_app_server.py` (MIT License). The client remains a
small synchronous stdio transport; LAOS-specific review policy lives elsewhere.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CodexAppServerError(RuntimeError):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"codex app-server error {self.code}: {self.message}"


@dataclass
class _Pending:
    queue: queue.Queue
    method: str
    sent_at: float = field(default_factory=time.time)


class CodexAppServerClient:
    """Synchronous JSON-RPC 2.0 client for `codex app-server` over stdio."""

    def __init__(
        self,
        codex_bin: str = "codex",
        codex_home: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        process_factory: Any = subprocess.Popen,
    ) -> None:
        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)
        if codex_home:
            spawn_env["CODEX_HOME"] = codex_home
        spawn_env.setdefault("RUST_LOG", "warn")

        self._proc = process_factory(
            [codex_bin, "app-server", *(extra_args or [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=spawn_env,
        )
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._notifications: queue.Queue = queue.Queue()
        self._server_requests: queue.Queue = queue.Queue()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._closed = False
        self._initialized = False

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    def initialize(
        self,
        client_name: str = "laos",
        client_title: str = "LAOS",
        client_version: str = "0.1",
        capabilities: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if self._initialized:
            raise RuntimeError("already initialized")
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": client_name,
                    "title": client_title,
                    "version": client_version,
                },
                "capabilities": capabilities or {},
            },
            timeout=timeout,
        )
        self.notify("initialized")
        self._initialized = True
        return result

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = _Pending(response_queue, method)
        self._send({"id": request_id, "method": method, "params": params or {}})
        try:
            message = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(
                f"codex app-server method {method!r} timed out after {timeout}s"
            ) from exc
        if "error" in message:
            error = message["error"] or {}
            raise CodexAppServerError(
                int(error.get("code", -1)),
                str(error.get("message", "")),
                error.get("data"),
            )
        result = message.get("result", {})
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self._send({"id": request_id, "result": result})

    def take_notification(self, timeout: float = 0.0) -> dict[str, Any] | None:
        try:
            return (
                self._notifications.get_nowait()
                if timeout <= 0
                else self._notifications.get(timeout=timeout)
            )
        except queue.Empty:
            return None

    def take_server_request(self, timeout: float = 0.0) -> dict[str, Any] | None:
        try:
            return (
                self._server_requests.get_nowait()
                if timeout <= 0
                else self._server_requests.get(timeout=timeout)
            )
        except queue.Empty:
            return None

    def stderr_tail(self, count: int = 20) -> list[str]:
        with self._stderr_lock:
            return list(self._stderr_lines[-count:])

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def close(self, timeout: float = 3.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=1.0)

    def __enter__(self) -> "CodexAppServerClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _send(self, value: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("codex app-server client is closed")
        if self._proc.stdin is None:
            raise RuntimeError("codex app-server stdin is unavailable")
        try:
            self._proc.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise RuntimeError("codex app-server stdin closed unexpectedly") from exc

    def _read_stdout(self) -> None:
        if self._proc.stdout is None:
            return
        for raw in iter(self._proc.stdout.readline, b""):
            if not raw:
                break
            try:
                message = json.loads(raw.strip())
            except json.JSONDecodeError:
                with self._stderr_lock:
                    self._stderr_lines.append(f"<non-json stdout> {raw[:200]!r}")
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and ("result" in message or "error" in message):
                with self._pending_lock:
                    pending = self._pending.pop(message["id"], None)
                if pending:
                    pending.queue.put_nowait(message)
            elif "id" in message and "method" in message:
                self._server_requests.put(message)
            elif "method" in message:
                self._notifications.put(message)

    def _read_stderr(self) -> None:
        if self._proc.stderr is None:
            return
        for raw in iter(self._proc.stderr.readline, b""):
            if not raw:
                break
            with self._stderr_lock:
                self._stderr_lines.append(raw.decode("utf-8", "replace").rstrip())
                if len(self._stderr_lines) > 500:
                    self._stderr_lines = self._stderr_lines[-500:]
