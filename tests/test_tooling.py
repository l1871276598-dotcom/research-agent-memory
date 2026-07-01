import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tooling import ToolExecutor, ToolRegistry


class Extensions:
    def __init__(self, response=None):
        self.response = response
        self.calls = []

    def invoke(self, hook, **kwargs):
        self.calls.append((hook, kwargs))
        return [self.response] if self.response is not None else []


class ToolingTests(unittest.TestCase):
    def test_registry_rejects_collision_and_exports_definitions(self):
        registry = ToolRegistry()
        registry.register(
            "echo",
            lambda value: value,
            description="Return a value",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        )
        with self.assertRaises(ValueError):
            registry.register("echo", lambda: None)
        self.assertEqual(registry.names(), ["echo"])
        self.assertEqual(registry.definitions()[0]["function"]["name"], "echo")
        self.assertEqual(registry.call("echo", {"value": "x"}), "x")

    def test_executor_respects_allowlist_and_extension_denial(self):
        registry = ToolRegistry()
        registry.register("echo", lambda value: value)
        denied = ToolExecutor(registry, allowed=set()).execute("echo", {"value": "x"})
        self.assertFalse(denied["success"])

        extensions = Extensions({"action": "deny", "reason": "policy"})
        result = ToolExecutor(registry, extensions=extensions).execute("echo", {"value": "x"})
        self.assertEqual(result["error"], "policy")

    def test_executor_requires_approval_for_sensitive_command(self):
        registry = ToolRegistry()
        registry.register("terminal", lambda command: command, mutating=True)
        executor = ToolExecutor(registry)
        result = executor.execute("terminal", {"command": "sudo apt update"})
        self.assertEqual(result["approval"], "review")

        executor.approval.approve("sudo apt update", "once")
        approved = executor.execute("terminal", {"command": "sudo apt update"})
        self.assertTrue(approved["success"])

    def test_repeated_failure_surfaces_guardrail_state(self):
        registry = ToolRegistry()
        registry.register("fail", lambda: (_ for _ in ()).throw(RuntimeError("no")))
        executor = ToolExecutor(registry)
        first = executor.execute("fail")
        second = executor.execute("fail")
        self.assertEqual(first["guardrail"]["action"], "allow")
        self.assertEqual(second["guardrail"]["action"], "warn")


if __name__ == "__main__":
    unittest.main()
