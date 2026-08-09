import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from laos import build_application
from memory import init_store
from models import ModelBackendRegistry, OpenAICompatibleBackend, build_model_backend


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class FakeBackend:
    backend_id = "fake"

    def __init__(self):
        self.calls = []

    def review(self, messages):
        self.calls.append(messages)
        return '{"memory_candidates":[],"skill_candidates":[],"nothing_to_save":true}'


class ModelBackendTests(unittest.TestCase):
    def test_registry_supports_future_provider_factories(self):
        registry = ModelBackendRegistry()
        registry.register("custom", lambda value=1: {"value": value})
        self.assertEqual(registry.create("custom", {"value": 4}), {"value": 4})
        self.assertEqual(registry.names(), ["custom"])
        with self.assertRaises(ValueError):
            registry.register("custom", lambda: None)

    def test_openai_compatible_backend_supports_chat_and_json_review(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"memory_candidates":[],"skill_candidates":[],"nothing_to_save":true}',
                                "tool_calls": [],
                            }
                        }
                    ]
                }
            )

        backend = OpenAICompatibleBackend(
            base_url="http://localhost:1234/v1",
            model="local-model",
            opener=opener,
        )
        result = backend.review([{"role": "user", "content": "review"}])

        self.assertIn("nothing_to_save", result)
        request, timeout = requests[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://localhost:1234/v1/chat/completions")
        self.assertEqual(payload["model"], "local-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(timeout, 120.0)

    def test_factory_defaults_to_codex_and_accepts_openai_compatible_config(self):
        default = build_model_backend()
        self.assertEqual(default.backend_id, "codex")
        self.assertTrue(default.supports("chat"))
        self.assertTrue(default.supports("review"))
        default.executor = lambda messages: "bounded result"
        self.assertEqual(
            default.complete([{"role": "user", "content": "task"}]),
            {"content": "bounded result", "tool_calls": []},
        )
        with self.assertRaisesRegex(ValueError, "does not accept tools"):
            default.complete(
                [{"role": "user", "content": "task"}],
                tools=[{"type": "function"}],
            )

        configured = build_model_backend(
            {
                "backend": "openai_compatible",
                "options": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "local-model",
                },
            }
        )
        self.assertEqual(configured.backend_id, "openai_compatible")

    def test_laos_uses_injected_backend_instead_of_codex(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            backend = FakeBackend()
            application = build_application(data, state, backend)
            task = {
                "type": "reflection.record",
                "workspace": "personal",
                "confidentiality": "personal",
                "input": {
                    "session_id": "session-one",
                    "messages": [{"role": "user", "content": "Remember minimal code."}],
                },
            }
            result = None
            for _ in range(10):
                result = application.run(task)

            self.assertEqual(result["output"]["status"], "reviewed")
            self.assertEqual(len(backend.calls), 1)


if __name__ == "__main__":
    unittest.main()
