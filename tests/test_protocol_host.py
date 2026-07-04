import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol_host import build_protocol_host


class FakeFastMCP:
    def __init__(self, name, instructions, **options):
        self.name = name
        self.instructions = instructions
        self.options = options
        self.tools = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class Facade:
    def __init__(self, checkpoint_enabled=True):
        self.checkpoint_capture = object() if checkpoint_enabled else None

    def run_task(self, task):
        return {"task": task}

    def capture_checkpoint(self, **values):
        return {"captured": values}

    def session_search(self, query, workspace=None, project=None, limit=20):
        return []

    def session_get(self, session_id):
        return {"id": session_id}

    def procedure_list(self, status="candidate"):
        return []


def mcp_modules():
    module = types.ModuleType("mcp.server.fastmcp")
    module.FastMCP = FakeFastMCP
    return {
        "mcp": types.ModuleType("mcp"),
        "mcp.server": types.ModuleType("mcp.server"),
        "mcp.server.fastmcp": module,
    }


class ProtocolHostTests(unittest.TestCase):
    def test_checkpoint_tool_is_explicit_and_routes_all_evidence_fields(self):
        with mock.patch.dict(sys.modules, mcp_modules()):
            host = build_protocol_host(Facade())

        self.assertIn("explicit, not passive", host.instructions)
        self.assertIn("laos_capture_checkpoint", host.tools)
        tool = host.tools["laos_capture_checkpoint"]
        result = tool(
            session_alias="conversation-one",
            user_message="Question",
            assistant_response="Answer",
            checkpoint_id="checkpoint-one",
            source_conversation_id="source-conversation",
            source_user_message_id="source-user",
            source_assistant_message_id="source-assistant",
            force_review=True,
        )
        values = result["captured"]
        self.assertEqual(values["session_alias"], "conversation-one")
        self.assertEqual(values["assistant_response"], "Answer")
        self.assertEqual(values["checkpoint_id"], "checkpoint-one")
        self.assertEqual(values["source_conversation_id"], "source-conversation")
        self.assertTrue(values["force_review"])
        self.assertIn("cannot inspect the conversation automatically", tool.__doc__)

    def test_checkpoint_tool_is_absent_when_capture_is_disabled(self):
        with mock.patch.dict(sys.modules, mcp_modules()):
            host = build_protocol_host(Facade(checkpoint_enabled=False))

        self.assertNotIn("checkpoint", host.instructions)
        self.assertNotIn("laos_capture_checkpoint", host.tools)

    def test_remote_server_options_are_passed_to_fastmcp(self):
        options = {
            "host": "127.0.0.1",
            "port": 8766,
            "streamable_http_path": "/mcp",
            "json_response": True,
            "stateless_http": True,
        }
        with mock.patch.dict(sys.modules, mcp_modules()):
            host = build_protocol_host(Facade(), server_options=options)

        self.assertEqual(host.options, options)


if __name__ == "__main__":
    unittest.main()
