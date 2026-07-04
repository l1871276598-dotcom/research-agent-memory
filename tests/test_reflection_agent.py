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
from agents.reflection import ReflectionAgent


PASS_REFLECTION = """# Reflection

## Task

Validate migration

## Outcome

pass

## Result Evidence

Migration completed

## Error Evidence

None

## What Worked

Version preflight passed

## What Failed

Pending human or agent review.

## Root Cause

Pending human or agent review.

## Next Change

Keep the version preflight
"""

FAIL_REFLECTION = """# Reflection

## Task

Validate migration

## Outcome

fail

## Result Evidence

Migration stopped

## Error Evidence

Target version mismatch

## What Worked

Pending human or agent review.

## What Failed

Version preflight was missing

## Root Cause

Pending human or agent review.

## Next Change

Pending human or agent review.
"""


class ReflectionAgentTests(unittest.TestCase):
    def fake_agent(self, tmp, run_id, outcome, reflection_text):
        state = Path(tmp) / "state"
        run_dir = state / "loop_engineering" / "runs" / run_id
        run_dir.mkdir(parents=True)
        run_path = run_dir / "run.json"
        run_path.write_text("{}\n", encoding="utf-8")
        run_data = {"run_id": run_id, "outcome": outcome}

        def loader(received_state, received_run_id):
            self.assertEqual(Path(received_state), state)
            self.assertEqual(received_run_id, run_id)
            return {"run": run_path}, run_data, reflection_text

        return ReflectionAgent(state, loader=loader), state, run_dir

    def test_pass_reflection_publishes_deterministic_artifact_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "a" * 32
            agent, state, run_dir = self.fake_agent(tmp, run_id, "pass", PASS_REFLECTION)
            task = {"type": "loop.reflect", "input": {"run_id": run_id}}

            first = agent.run(task, {})
            second = agent.run(task, {})
            artifact = run_dir / "reflection_result.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))

        validate_result(first, agent, task)
        validate_result(second, agent, task)
        self.assertFalse(first["output"]["reused"])
        self.assertTrue(second["output"]["reused"])
        self.assertEqual(first["output"]["reflection"], second["output"]["reflection"])
        self.assertEqual(
            first["output"]["artifact"],
            f"loop_engineering/runs/{run_id}/reflection_result.json",
        )
        self.assertEqual(payload["contract_id"], "laos.reflection-result.v1")
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["reflection"]["what_happened"], "Migration completed")
        self.assertEqual(payload["reflection"]["what_worked"], "Version preflight passed")
        self.assertEqual(payload["reflection"]["what_failed"], "unknown")
        self.assertEqual(payload["reflection"]["likely_cause"], "unknown")
        self.assertEqual(payload["reflection"]["reusable_lesson"], "Keep the version preflight")
        self.assertEqual(first["candidates"], [])
        self.assertTrue(first["requires_review"])
        self.assertFalse(first["output"]["applied"])

    def test_fail_reflection_prefers_error_and_does_not_invent_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "b" * 32
            agent, _, _ = self.fake_agent(tmp, run_id, "fail", FAIL_REFLECTION)
            output = agent.run(
                {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
            )["output"]["reflection"]

        self.assertEqual(output["what_happened"], "Target version mismatch")
        self.assertEqual(output["what_worked"], "unknown")
        self.assertEqual(output["what_failed"], "Version preflight was missing")
        self.assertEqual(output["likely_cause"], "unknown")
        self.assertEqual(output["reusable_lesson"], "unknown")
        self.assertIn("likely_cause", output["unknown_fields"])
        self.assertFalse(
            any(item["kind"] == "root_cause" for item in output["evidence"])
        )

    def test_missing_direct_evidence_uses_unknown(self):
        reflection = PASS_REFLECTION.replace("Migration completed", "None").replace(
            "Version preflight passed", "Pending human or agent review."
        ).replace("Keep the version preflight", "Pending human or agent review.")
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "c" * 32
            agent, _, _ = self.fake_agent(tmp, run_id, "pass", reflection)
            output = agent.run(
                {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
            )["output"]["reflection"]

        self.assertEqual(output["what_happened"], "unknown")
        self.assertEqual(output["what_worked"], "unknown")
        self.assertEqual(output["reusable_lesson"], "unknown")
        self.assertEqual(
            output["unknown_fields"],
            [
                "what_happened",
                "what_worked",
                "what_failed",
                "likely_cause",
                "reusable_lesson",
            ],
        )

    def test_invalid_input_outcome_mismatch_and_artifact_conflict_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "d" * 32
            agent, _, _ = self.fake_agent(tmp, run_id, "pass", FAIL_REFLECTION)
            valid_task = {"type": "loop.reflect", "input": {"run_id": run_id}}

            invalid = [
                {"type": "loop.reflect"},
                {"type": "loop.reflect", "input": {}},
                {"type": "loop.reflect", "input": {"run_id": "../escape"}},
                {
                    "type": "loop.reflect",
                    "input": {"run_id": run_id, "extra": True},
                },
                {"type": "memory.search", "input": {"run_id": run_id}},
            ]
            for task in invalid:
                with self.subTest(task=task):
                    with self.assertRaises(ValueError):
                        agent.run(task, {})

            with self.assertRaises(ValueError):
                agent.run(valid_task, {})

            corrected, _, _ = self.fake_agent(
                Path(tmp) / "other", "e" * 32, "pass", PASS_REFLECTION
            )
            corrected_task = {
                "type": "loop.reflect",
                "input": {"run_id": "e" * 32},
            }
            corrected.run(corrected_task, {})
            artifact = (
                Path(tmp)
                / "other"
                / "state"
                / "loop_engineering"
                / "runs"
                / ("e" * 32)
                / "reflection_result.json"
            )
            artifact.write_text('{"tampered": true}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                corrected.run(corrected_task, {})

    def test_real_loop_run_generates_only_local_reflection_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state = Path(tmp) / "state"
            result = subprocess.run(
                [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finalized = memory_agent.finalize_memory(
                root,
                "Validate migration",
                "Migration stopped",
                state_dir=state,
                outcome="fail",
                error="Target version mismatch",
                reflection="Version preflight was missing",
                policy_suggestions=["Validate target version before migration"],
            )
            run_id = finalized["loop_run"]["run_id"]
            run_dir = state / finalized["loop_run"]["path"]
            before = {
                path.name: path.read_bytes()
                for path in run_dir.iterdir()
                if path.is_file()
            }
            candidate_paths = list((root / "memory" / "sessions").glob("*.md"))
            candidate_before = [path.read_bytes() for path in candidate_paths]

            agent = ReflectionAgent(state)
            task = {"type": "loop.reflect", "input": {"run_id": run_id}}
            reflected = agent.run(task, {})

            after_originals = {
                name: (run_dir / name).read_bytes() for name in before
            }
            candidate_after = [path.read_bytes() for path in candidate_paths]
            artifact_exists = (run_dir / "reflection_result.json").is_file()
            rules_exist = (root / "memory_rules.md").exists()

        validate_result(reflected, agent, task)
        output = reflected["output"]["reflection"]
        self.assertEqual(output["what_happened"], "Target version mismatch")
        self.assertEqual(output["what_failed"], "Version preflight was missing")
        self.assertEqual(output["likely_cause"], "unknown")
        self.assertEqual(output["reusable_lesson"], "unknown")
        self.assertEqual(before, after_originals)
        self.assertEqual(candidate_before, candidate_after)
        self.assertTrue(artifact_exists)
        self.assertFalse(rules_exist)

    def test_v09_application_and_cli_route_loop_reflect(self):
        import laos

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state = Path(tmp) / "state"
            result = subprocess.run(
                [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finalized = memory_agent.finalize_memory(
                root,
                "Validate migration",
                "Migration completed",
                state_dir=state,
                outcome="pass",
                reflection="Version preflight passed",
            )
            run_id = finalized["loop_run"]["run_id"]
            application = laos.build_application(root, state)
            selected = application.registry.select({"type": "loop.reflect"})

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
                        {"type": "loop.reflect", "input": {"run_id": run_id}},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            payload = json.loads(cli.stdout)

        self.assertEqual(selected.agent_id, "reflection_agent")
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(payload["agent_id"], "reflection_agent")
        self.assertEqual(payload["task_type"], "loop.reflect")
        self.assertEqual(payload["output"]["run_id"], run_id)
        self.assertTrue(payload["output"]["artifact"].endswith("reflection_result.json"))
        self.assertEqual(payload["candidates"], [])
        self.assertTrue(payload["requires_review"])


if __name__ == "__main__":
    unittest.main()
