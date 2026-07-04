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
LAOS_CLI = SOURCE_DIR / "laos.py"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory_agent
from agents.base import validate_result
from agents.policy import PolicyAgent
from agents.reflection import ReflectionAgent


class PolicyAgentTests(unittest.TestCase):
    def init_root(self, tmp):
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

    def make_run(
        self,
        root,
        state,
        *,
        outcome="pass",
        reflection="Version preflight evidence",
        lesson=None,
        policy_suggestions=None,
    ):
        finalized = memory_agent.finalize_memory(
            root,
            "Validate migration",
            "Migration completed" if outcome == "pass" else "Migration stopped",
            state_dir=state,
            outcome=outcome,
            error=None if outcome == "pass" else "Target version mismatch",
            reflection=reflection,
            policy_suggestions=policy_suggestions,
        )
        run_id = finalized["loop_run"]["run_id"]
        run_dir = state / finalized["loop_run"]["path"]
        if lesson is not None:
            reflection_path = run_dir / "reflection.md"
            text = reflection_path.read_text(encoding="utf-8")
            marker = "## Next Change\n\nPending human or agent review."
            self.assertIn(marker, text)
            reflection_path.write_text(
                text.replace(marker, f"## Next Change\n\n{lesson}"),
                encoding="utf-8",
            )
        ReflectionAgent(state).run(
            {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
        )
        return run_id, run_dir

    @staticmethod
    def rule_text(text):
        policy_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return (
            "# Memory Rules\n\n"
            f"## rule-{policy_id}\n\n"
            f"- Policy: {text}\n"
            "- Source run: historical\n"
            "- Outcome: pass\n"
            "- Approved: 2026-07-01T00:00:00+00:00\n"
        )

    def test_policy_candidates_use_only_reflection_result_and_have_stable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            run_id, run_dir = self.make_run(
                root,
                state,
                lesson=lesson,
                policy_suggestions=["This legacy suggestion must be ignored"],
            )
            agent = PolicyAgent(root, state)
            task = {"type": "loop.suggest-policies", "input": {"run_id": run_id}}

            first = agent.run(task, {})
            second = agent.run(task, {})
            artifact = json.loads(
                (run_dir / "policy_candidates.json").read_text(encoding="utf-8")
            )
            review = (run_dir / "policy_review.md").read_text(encoding="utf-8")
            legacy = (run_dir / "policy_suggestions.md").read_text(encoding="utf-8")
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            rules_exist = (root / "memory_rules.md").exists()

        validate_result(first, agent, task)
        self.assertFalse(first["output"]["reused"])
        self.assertTrue(second["output"]["reused"])
        self.assertEqual(first["output"]["candidate_count"], 1)
        candidate = artifact["candidates"][0]
        expected_id = hashlib.sha256(lesson.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(candidate["policy_id"], expected_id)
        self.assertEqual(candidate["text"], lesson)
        self.assertEqual(candidate["status"], "review_required")
        self.assertIn(lesson, review)
        self.assertNotIn("legacy suggestion", review)
        self.assertIn("legacy suggestion", legacy)
        self.assertEqual(run_data["policy_suggestion_ids"], [expected_id])
        self.assertFalse(artifact["applied"])
        self.assertFalse(rules_exist)
        self.assertEqual(first["candidates"], [])

    def test_exact_approved_rule_is_classified_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            (root / "memory_rules.md").write_text(
                self.rule_text(lesson), encoding="utf-8"
            )
            run_id, run_dir = self.make_run(root, state, lesson=lesson)

            result = PolicyAgent(root, state).run(
                {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
            )
            artifact = json.loads(
                (run_dir / "policy_candidates.json").read_text(encoding="utf-8")
            )
            review = (run_dir / "policy_review.md").read_text(encoding="utf-8")

        candidate = artifact["candidates"][0]
        self.assertEqual(candidate["status"], "duplicate")
        self.assertEqual(candidate["duplicate_of"], candidate["policy_id"])
        self.assertEqual(result["output"]["duplicate_count"], 1)
        self.assertEqual(result["output"]["reviewable_count"], 0)
        self.assertIn("duplicate:", review)
        self.assertNotIn("- [ ]", review)

    def test_explicit_opposite_directives_are_classified_conflicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            approved = "forbid: deploy without tests"
            proposed = "require: deploy without tests"
            (root / "memory_rules.md").write_text(
                self.rule_text(approved), encoding="utf-8"
            )
            run_id, run_dir = self.make_run(root, state, lesson=proposed)

            result = PolicyAgent(root, state).run(
                {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
            )
            artifact = json.loads(
                (run_dir / "policy_candidates.json").read_text(encoding="utf-8")
            )
            review = (run_dir / "policy_review.md").read_text(encoding="utf-8")

        candidate = artifact["candidates"][0]
        approved_id = hashlib.sha256(approved.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(candidate["status"], "conflicted")
        self.assertEqual(candidate["conflicts_with"], [approved_id])
        self.assertEqual(result["output"]["conflicted_count"], 1)
        self.assertEqual(result["output"]["reviewable_count"], 0)
        self.assertIn("conflicted:", review)
        self.assertNotIn("- [ ]", review)

    def test_unknown_reusable_lesson_produces_empty_review_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            run_id, run_dir = self.make_run(
                root,
                state,
                outcome="fail",
                reflection="Version preflight was missing",
                lesson=None,
            )

            result = PolicyAgent(root, state).run(
                {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
            )
            artifact = json.loads(
                (run_dir / "policy_candidates.json").read_text(encoding="utf-8")
            )
            review = (run_dir / "policy_review.md").read_text(encoding="utf-8")
            rules_exist = (root / "memory_rules.md").exists()

        self.assertEqual(artifact["candidates"], [])
        self.assertFalse(artifact["review_required"])
        self.assertEqual(result["output"]["candidate_count"], 0)
        self.assertIn("No reviewable policy candidates.", review)
        self.assertFalse(rules_exist)

    def test_missing_or_tampered_reflection_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "Validate migration",
                "Migration completed",
                state_dir=state,
                outcome="pass",
                reflection="Version preflight evidence",
            )
            run_id = finalized["loop_run"]["run_id"]
            run_dir = state / finalized["loop_run"]["path"]
            agent = PolicyAgent(root, state)
            task = {"type": "loop.suggest-policies", "input": {"run_id": run_id}}

            with self.assertRaises(ValueError):
                agent.run(task, {})

            ReflectionAgent(state).run(
                {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
            )
            reflection_path = run_dir / "reflection_result.json"
            reflection_path.write_text('{"tampered": true}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                agent.run(task, {})
            policy_artifact_exists = (run_dir / "policy_candidates.json").exists()
            review_artifact_exists = (run_dir / "policy_review.md").exists()

        self.assertFalse(policy_artifact_exists)
        self.assertFalse(review_artifact_exists)

    def test_v09_cli_routes_policy_agent_without_memory_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            run_id, run_dir = self.make_run(
                root,
                state,
                lesson="Keep the version preflight",
            )
            memory_before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in (root / "memory").rglob("*.md")
            }

            cli = subprocess.run(
                [
                    sys.executable,
                    str(LAOS_CLI),
                    "--root",
                    str(root),
                    "--state-dir",
                    str(state),
                    "--task-json",
                    json.dumps(
                        {
                            "type": "loop.suggest-policies",
                            "input": {"run_id": run_id},
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            payload = json.loads(cli.stdout)
            memory_after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in (root / "memory").rglob("*.md")
            }
            policy_artifact_exists = (run_dir / "policy_candidates.json").is_file()
            review_artifact_exists = (run_dir / "policy_review.md").is_file()
            rules_exist = (root / "memory_rules.md").exists()

        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(payload["agent_id"], "policy_agent")
        self.assertEqual(payload["task_type"], "loop.suggest-policies")
        self.assertEqual(payload["output"]["run_id"], run_id)
        self.assertTrue(policy_artifact_exists)
        self.assertTrue(review_artifact_exists)
        self.assertEqual(memory_before, memory_after)
        self.assertFalse(rules_exist)
        self.assertEqual(payload["candidates"], [])
        self.assertTrue(payload["requires_review"])


if __name__ == "__main__":
    unittest.main()
