import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
MEMORY_CLI = SOURCE_DIR / "memory.py"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory_agent
from agents.policy import PolicyAgent
from agents.reflection import ReflectionAgent


class StructuredReflectionEvidenceTests(unittest.TestCase):
    def init_store(self, tmp):
        root = Path(tmp) / "data"
        state = Path(tmp) / "state"
        result = subprocess.run(
            [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root, state

    def test_real_failed_loop_produces_verifiable_reusable_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            values = {
                "root": root,
                "task": "Run migration without target-version preflight",
                "result": "Migration stopped before writes",
                "state_dir": state,
                "outcome": "fail",
                "error": "Target version mismatch",
                "reflection": "The migration path reached validation and stopped safely",
                "root_cause": "The workflow omitted the target-version preflight",
                "next_change": "Require target-version preflight before migration",
            }

            first = memory_agent.finalize_memory(**values)
            second = memory_agent.finalize_memory(**values)
            run_id = first["loop_run"]["run_id"]
            reflected = ReflectionAgent(state).run(
                {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
            )
            suggested = PolicyAgent(root, state).run(
                {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
            )
            run_dir = state / first["loop_run"]["path"]
            reflection_artifact = json.loads(
                (run_dir / "reflection_result.json").read_text(encoding="utf-8")
            )
            policy_artifact = json.loads(
                (run_dir / "policy_candidates.json").read_text(encoding="utf-8")
            )

        self.assertFalse(first["loop_run"]["reused"])
        self.assertTrue(second["loop_run"]["reused"])
        reflection = reflected["output"]["reflection"]
        self.assertEqual(
            reflection["likely_cause"],
            "The workflow omitted the target-version preflight",
        )
        self.assertEqual(
            reflection["reusable_lesson"],
            "Require target-version preflight before migration",
        )
        self.assertNotIn("likely_cause", reflection["unknown_fields"])
        self.assertNotIn("reusable_lesson", reflection["unknown_fields"])
        self.assertEqual(reflection_artifact["reflection"], reflection)
        self.assertEqual(suggested["output"]["reviewable_count"], 1)
        self.assertEqual(
            policy_artifact["candidates"][0]["normalized_text"],
            "Require target-version preflight before migration",
        )
        self.assertEqual(policy_artifact["candidates"][0]["status"], "review_required")
        self.assertFalse(policy_artifact["applied"])

    def test_missing_next_change_remains_unknown_and_changes_idempotency_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            common = {
                "root": root,
                "task": "Validate migration",
                "result": "Migration stopped",
                "state_dir": state,
                "outcome": "fail",
                "error": "Target version mismatch",
                "reflection": "Version preflight was missing",
                "root_cause": "The workflow omitted a preflight",
            }
            without_lesson = memory_agent.finalize_memory(**common)
            with_lesson = memory_agent.finalize_memory(
                **common,
                next_change="Require target-version preflight before migration",
            )
            reflected = ReflectionAgent(state).run(
                {
                    "type": "loop.reflect",
                    "input": {"run_id": without_lesson["loop_run"]["run_id"]},
                },
                {},
            )

        self.assertNotEqual(
            without_lesson["loop_run"]["run_id"],
            with_lesson["loop_run"]["run_id"],
        )
        self.assertEqual(
            reflected["output"]["reflection"]["reusable_lesson"], "unknown"
        )

    def test_absent_structured_evidence_preserves_stage10_v2_identity(self):
        task = "Validate migration"
        result = "Migration stopped"
        outcome = "fail"
        error = "Target version mismatch"
        reflection = "Version preflight was missing"
        policies = ["Require target-version preflight before migration"]
        legacy_payload = {
            "task": task,
            "result": result,
            "outcome": outcome,
            "error": error,
            "reflection": reflection,
            "policy_suggestions": policies,
        }
        expected = hashlib.sha256(
            json.dumps(
                legacy_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        actual = memory_agent._loop_input_digest(
            task,
            result,
            outcome,
            error,
            reflection,
            policies,
        )
        extended = memory_agent._loop_input_digest(
            task,
            result,
            outcome,
            error,
            reflection,
            policies,
            next_change="Require target-version preflight before migration",
        )

        self.assertEqual(actual, expected)
        self.assertNotEqual(extended, expected)


if __name__ == "__main__":
    unittest.main()
