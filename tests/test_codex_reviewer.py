import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from runtime.codex.reviewer import CodexConversationReviewer, _json_text, _thread_id


class FakeClient:
    instances = []
    notifications = []
    requests = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.responses = []
        self.closed = False
        self._notifications = list(type(self).notifications)
        self._requests = list(type(self).requests)
        type(self).instances.append(self)

    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        return {"ok": True}

    def request(self, method, params=None, timeout=0):
        self.calls.append((method, params, timeout))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    def take_server_request(self, timeout=0):
        return self._requests.pop(0) if self._requests else None

    def take_notification(self, timeout=0):
        return self._notifications.pop(0) if self._notifications else None

    def respond(self, request_id, result):
        self.responses.append((request_id, result))

    def is_alive(self):
        return True

    def close(self):
        self.closed = True


class CodexReviewerTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances = []
        FakeClient.notifications = []
        FakeClient.requests = []

    def test_thread_id_accepts_current_and_legacy_shapes(self):
        self.assertEqual(_thread_id({"thread": {"id": "a"}}), "a")
        self.assertEqual(_thread_id({"thread": {"sessionId": "b"}}), "b")
        self.assertEqual(_thread_id({"threadId": "c"}), "c")
        with self.assertRaises(RuntimeError):
            _thread_id({})

    def test_json_text_accepts_plain_and_fenced_json(self):
        value = '{"memory_candidates":[],"skill_candidates":[],"nothing_to_save":true}'
        self.assertEqual(_json_text(value), value)
        self.assertEqual(_json_text(f"```json\n{value}\n```"), value)
        with self.assertRaises(json.JSONDecodeError):
            _json_text("not-json")

    def test_reviewer_uses_ephemeral_read_only_thread_and_returns_json(self):
        payload = '{"memory_candidates":[],"skill_candidates":[],"nothing_to_save":true}'
        FakeClient.notifications = [
            {
                "method": "item/completed",
                "params": {"item": {"type": "agentMessage", "text": payload}},
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed"}},
            },
        ]
        reviewer = CodexConversationReviewer(client_factory=FakeClient, timeout=2)

        result = reviewer([{"role": "user", "content": "remember this"}])

        self.assertEqual(result, payload)
        client = FakeClient.instances[0]
        thread_call = next(call for call in client.calls if call[0] == "thread/start")
        self.assertTrue(thread_call[1]["ephemeral"])
        self.assertEqual(thread_call[1]["approvalPolicy"], "never")
        self.assertEqual(thread_call[1]["sandbox"], "readOnly")
        turn_call = next(call for call in client.calls if call[0] == "turn/start")
        self.assertEqual(turn_call[1]["sandboxPolicy"], {"type": "readOnly"})
        self.assertIn("Do not call tools", turn_call[1]["input"][0]["text"])
        self.assertTrue(client.closed)

    def test_reviewer_declines_requests_and_rejects_tool_items(self):
        FakeClient.requests = [
            {"id": 7, "method": "item/commandExecution/requestApproval"}
        ]
        FakeClient.notifications = [
            {
                "method": "item/completed",
                "params": {"item": {"type": "commandExecution"}},
            }
        ]
        reviewer = CodexConversationReviewer(client_factory=FakeClient, timeout=2)

        with self.assertRaises(RuntimeError):
            reviewer([{"role": "user", "content": "review"}])

        self.assertEqual(FakeClient.instances[0].responses, [(7, {"decision": "decline"})])
        self.assertTrue(FakeClient.instances[0].closed)

    def test_reviewer_rejects_non_completed_turn(self):
        FakeClient.notifications = [
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "failed"}},
            }
        ]
        reviewer = CodexConversationReviewer(client_factory=FakeClient, timeout=2)

        with self.assertRaisesRegex(RuntimeError, "status failed"):
            reviewer([{"role": "user", "content": "review"}])


if __name__ == "__main__":
    unittest.main()
