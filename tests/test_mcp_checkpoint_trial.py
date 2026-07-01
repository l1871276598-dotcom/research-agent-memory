import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bridge import McpCheckpointCapture, build_bridge_pipeline


SPEC = importlib.util.spec_from_file_location(
    "mcp_checkpoint_trial",
    ROOT / "tools" / "mcp_checkpoint_trial.py",
)
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)

TOKEN = "trial-internal-token-123456789"


class McpCheckpointTrialTests(unittest.TestCase):
    def test_prepare_initializes_store_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            result = TRIAL.prepare_trial(
                data,
                state,
                workspace="personal",
                expected_checkpoints=5,
                project="validation",
            )
            manifest = TRIAL.load_manifest(state)

            self.assertTrue((Path(data) / ".research-agent-root").is_file())
            self.assertEqual(manifest["expected_checkpoints"], 5)
            self.assertEqual(manifest["project"], "validation")
            self.assertFalse(manifest["model_review_enabled"])
            self.assertIn("laos_capture_checkpoint exactly once", result["chatgpt_instruction"])
            self.assertIn("mcp_checkpoint_trial.py serve", result["next_command"])

    def test_report_never_claims_passive_or_lossless_capture(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            TRIAL.prepare_trial(
                data,
                state,
                workspace="personal",
                expected_checkpoints=1,
            )
            pipeline = build_bridge_pipeline(
                data,
                state,
                token=TOKEN,
                allowed_sources={"chatgpt-mcp"},
                default_scope={
                    "workspace": "personal",
                    "project": "laos-checkpoint-test",
                    "confidentiality": "personal",
                },
            )
            capture = McpCheckpointCapture(pipeline, token=TOKEN)
            capture.capture(
                session_alias="trial-session",
                checkpoint_id="checkpoint-one",
                user_message="Question",
                assistant_response="Answer",
            )

            result = TRIAL.report_trial(state)
            report = result["automated_report"]
            self.assertEqual(result["classification"], "explicit_checkpoint_candidate")
            self.assertTrue(report["checkpoint_capture_ready"])
            self.assertFalse(report["lossless_conversation_capture_proven"])
            self.assertTrue(result["empirical_decision_pending"])
            self.assertIsNone(
                result["required_manual_evidence"]["tool_called_every_expected_turn"]
            )
            self.assertIn("Never classify", result["decision_rule"])

    def test_prepare_rejects_invalid_expected_count(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            with self.assertRaisesRegex(ValueError, "positive integer"):
                TRIAL.prepare_trial(
                    data,
                    state,
                    workspace="personal",
                    expected_checkpoints=0,
                )


if __name__ == "__main__":
    unittest.main()
