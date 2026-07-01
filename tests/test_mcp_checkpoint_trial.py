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


SPEC = importlib.util.spec_from_file_location(
    "mcp_checkpoint_trial",
    ROOT / "tools" / "mcp_checkpoint_trial.py",
)
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)

TOKEN = "trial-internal-token-123456789"


class McpCheckpointTrialTests(unittest.TestCase):
    def prepare_completed_trial(self, data, state):
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
            self.assertIsNone(manifest["classification"])
            self.assertIn("laos_capture_checkpoint exactly once", result["chatgpt_instruction"])
            self.assertIn("mcp_checkpoint_trial.py serve", result["next_command"])

    def test_report_never_claims_passive_or_lossless_capture(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            self.prepare_completed_trial(data, state)

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

    def test_decision_accepts_only_when_automated_and_manual_evidence_pass(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            self.prepare_completed_trial(data, state)

            result = TRIAL.decide_trial(
                state,
                tool_called_every_expected_turn=True,
                assistant_hash_matches_final_display_every_turn=True,
                write_confirmation_burden_acceptable=True,
            )
            manifest = TRIAL.load_manifest(state)

            self.assertTrue(result["accepted"])
            self.assertEqual(
                result["classification"],
                "formal_explicit_checkpoint_channel",
            )
            self.assertFalse(result["lossless_conversation_capture_proven"])
            self.assertEqual(result["scope"], "explicit checkpoint synchronization only")
            self.assertEqual(manifest["status"], "decided")
            self.assertEqual(manifest["classification"], result["classification"])
            self.assertIsNotNone(manifest["decided_at"])
            self.assertFalse(TRIAL.report_trial(state)["empirical_decision_pending"])

    def test_decision_rejects_when_confirmation_burden_is_unacceptable(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            self.prepare_completed_trial(data, state)

            result = TRIAL.decide_trial(
                state,
                tool_called_every_expected_turn=True,
                assistant_hash_matches_final_display_every_turn=True,
                write_confirmation_burden_acceptable=False,
            )

            self.assertFalse(result["accepted"])
            self.assertEqual(
                result["classification"],
                "rejected_for_formal_checkpoint_channel",
            )

    def test_decision_rejects_when_automated_evidence_is_incomplete(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            TRIAL.prepare_trial(
                data,
                state,
                workspace="personal",
                expected_checkpoints=2,
            )
            pipeline = build_bridge_pipeline(
                data,
                state,
                token=TOKEN,
                allowed_sources={"chatgpt-mcp"},
                default_scope={"workspace": "personal", "confidentiality": "personal"},
            )
            McpCheckpointCapture(pipeline, token=TOKEN).capture(
                session_alias="trial-session",
                checkpoint_id="checkpoint-one",
                user_message="Question",
                assistant_response="Answer",
            )

            result = TRIAL.decide_trial(
                state,
                tool_called_every_expected_turn=True,
                assistant_hash_matches_final_display_every_turn=True,
                write_confirmation_burden_acceptable=True,
            )

            self.assertFalse(result["accepted"])
            self.assertEqual(
                result["classification"],
                "insufficient_automated_evidence",
            )

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
