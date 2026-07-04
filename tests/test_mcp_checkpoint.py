import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bridge import McpCheckpointCapture, build_bridge_pipeline
from memory import init_store


SPEC = importlib.util.spec_from_file_location(
    "checkpoint_validation",
    ROOT / "tools" / "checkpoint_validation.py",
)
CHECKPOINT_VALIDATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKPOINT_VALIDATION)

TOKEN = "internal-checkpoint-token-123456"
SCOPE = {
    "workspace": "personal",
    "project": "laos",
    "profile": "personal",
    "confidentiality": "personal",
}


class McpCheckpointTests(unittest.TestCase):
    def build_capture(self, data, state):
        pipeline = build_bridge_pipeline(
            data,
            state,
            token=TOKEN,
            allowed_sources={"chatgpt-mcp"},
            default_scope=SCOPE,
        )
        return pipeline, McpCheckpointCapture(
            pipeline,
            token=TOKEN,
            account_id="chatgpt-account",
        )

    def test_explicit_checkpoint_persists_exchange_and_reports_limitations(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            pipeline, capture = self.build_capture(data, state)
            result = capture.capture(
                session_alias="conversation-alpha",
                user_message="Remember that I prefer minimal code.",
                assistant_response="Understood. I will keep code changes minimal.",
                conversation_summary="The user confirmed a coding preference.",
            )

            self.assertTrue(result["checkpoint_id"].startswith("derived-"))
            self.assertIsNotNone(result["session_id"])
            self.assertEqual(len(result["receipts"]), 2)
            self.assertEqual(len(result["projected"]), 2)
            evidence = result["capability_evidence"]
            self.assertEqual(evidence["capture_mode"], "explicit_tool_call")
            self.assertFalse(evidence["passive_conversation_access"])
            self.assertFalse(evidence["assistant_text_independently_observed"])
            self.assertFalse(evidence["source_conversation_id_provided"])
            self.assertFalse(evidence["stable_checkpoint_id_provided"])

            receipt = result["content_receipt"]
            self.assertEqual(
                receipt["assistant_response_sha256"],
                hashlib.sha256(
                    "Understood. I will keep code changes minimal.".encode("utf-8")
                ).hexdigest(),
            )
            session = pipeline.sessions.get(result["session_id"])
            self.assertEqual(session["workspace"], "personal")
            self.assertEqual(session["project"], "laos")
            self.assertEqual(
                [message["role"] for message in session["messages"]],
                ["user", "assistant"],
            )
            self.assertTrue(
                session["messages"][0]["metadata"]["caller_submitted"]
            )
            self.assertTrue(
                session["messages"][1]["metadata"]["bridge_generated"]
            )

    def test_retry_with_same_checkpoint_is_idempotent(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            pipeline, capture = self.build_capture(data, state)
            values = {
                "session_alias": "conversation-alpha",
                "user_message": "Question",
                "assistant_response": "Answer",
                "checkpoint_id": "checkpoint-one",
            }
            first = capture.capture(**values)
            second = capture.capture(**values)

            self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
            self.assertEqual(first["session_id"], second["session_id"])
            self.assertTrue(first["capability_evidence"]["stable_checkpoint_id_provided"])
            self.assertEqual(
                [receipt["status"] for receipt in second["receipts"]],
                ["duplicate", "duplicate"],
            )
            session = pipeline.sessions.get(first["session_id"])
            self.assertEqual(len(session["messages"]), 2)

    def test_real_source_ids_are_preserved_as_evidence_when_supplied(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            pipeline, capture = self.build_capture(data, state)
            result = capture.capture(
                session_alias="fallback-alias",
                user_message="Question",
                assistant_response="Answer",
                checkpoint_id="checkpoint-source-ids",
                source_conversation_id="real-conversation-id",
                source_user_message_id="real-user-message-id",
                source_assistant_message_id="real-assistant-message-id",
                branch_id="branch-two",
                version=2,
                force_review=True,
            )

            evidence = result["capability_evidence"]
            self.assertTrue(evidence["source_conversation_id_provided"])
            self.assertTrue(evidence["source_user_message_id_provided"])
            self.assertTrue(evidence["source_assistant_message_id_provided"])
            self.assertTrue(evidence["force_review"])
            self.assertEqual(len(result["receipts"]), 3)
            session = pipeline.sessions.get(result["session_id"])
            self.assertEqual(
                session["metadata"]["bridge_conversation_id"],
                "real-conversation-id",
            )
            self.assertEqual(
                session["messages"][0]["metadata"]["bridge_message_id"],
                "real-user-message-id",
            )
            self.assertEqual(
                session["messages"][1]["metadata"]["bridge_message_id"],
                "real-assistant-message-id",
            )

            report = CHECKPOINT_VALIDATION.build_report(state, expected_checkpoints=1)
            self.assertTrue(report["checkpoint_capture_ready"])
            self.assertFalse(report["lossless_conversation_capture_proven"])
            self.assertEqual(report["stable_checkpoint_ids"], 1)
            self.assertEqual(
                report["source_id_coverage"],
                {"conversation": 1, "user_message": 1, "assistant_message": 1},
            )
            self.assertEqual(report["complete_checkpoints"], 1)

    def test_report_fails_readiness_when_expected_calls_are_missing(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            _, capture = self.build_capture(data, state)
            capture.capture(
                session_alias="conversation-alpha",
                user_message="Question",
                assistant_response="Answer",
                checkpoint_id="checkpoint-one",
            )
            report = CHECKPOINT_VALIDATION.build_report(state, expected_checkpoints=2)
            self.assertFalse(report["checkpoint_capture_ready"])
            self.assertEqual(report["observed_checkpoints"], 1)
            self.assertIn("every expected turn", report["manual_checks_required"][0])

    def test_incomplete_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            _, capture = self.build_capture(data, state)
            with self.assertRaisesRegex(ValueError, "completed assistant response"):
                capture.capture(
                    session_alias="conversation-alpha",
                    user_message="Question",
                    assistant_response="Partial",
                    assistant_response_complete=False,
                )


if __name__ == "__main__":
    unittest.main()
