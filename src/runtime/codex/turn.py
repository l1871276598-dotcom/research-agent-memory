import time

from .app_server import CodexAppServerClient


class CodexTurnRunner:
    def __init__(self, cwd=None, client_factory=CodexAppServerClient):
        self.cwd = cwd
        self.client_factory = client_factory
        self.client = None
        self.thread_id = None

    def start(self):
        if self.thread_id:
            return self.thread_id
        self.client = self.client_factory()
        self.client.initialize(client_name="laos", client_title="LAOS", client_version="0.1")
        result = self.client.request("thread/start", {"cwd": self.cwd} if self.cwd else {}, timeout=15)
        thread = result.get("thread") or {}
        self.thread_id = thread.get("id") or thread.get("sessionId") or result.get("threadId")
        if not self.thread_id:
            raise RuntimeError("Codex did not return a thread id")
        return self.thread_id

    def run(self, text, timeout=600):
        self.start()
        started = self.client.request(
            "turn/start",
            {"threadId": self.thread_id, "input": [{"type": "text", "text": text}]},
            timeout=15,
        )
        turn_id = (started.get("turn") or {}).get("id")
        deadline = time.monotonic() + timeout
        final = ""
        tool_iterations = 0
        while time.monotonic() < deadline:
            request = self.client.take_server_request(timeout=0)
            if request is not None:
                self.client.respond(request.get("id"), {"decision": "decline"})
                continue
            note = self.client.take_notification(timeout=0.25)
            if note is None:
                if not self.client.is_alive():
                    raise RuntimeError("Codex exited during a turn")
                continue
            method = note.get("method")
            params = note.get("params") or {}
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    final = str(item.get("text") or "")
                elif item.get("type") in {"commandExecution", "fileChange", "mcpToolCall"}:
                    tool_iterations += 1
            elif method == "turn/completed":
                status = (params.get("turn") or {}).get("status")
                if status not in {None, "completed"}:
                    raise RuntimeError(f"Codex turn ended with status {status}")
                return {
                    "thread_id": self.thread_id,
                    "turn_id": turn_id,
                    "content": final,
                    "tool_iterations": tool_iterations,
                }
        raise TimeoutError("Codex turn timed out")

    def close(self):
        if self.client is not None:
            self.client.close()
        self.client = None
        self.thread_id = None
