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

from agents.base import validate_result
from agents.candidate_generator import LowRiskCandidateAgent
from agents.coordinator import LoopCoordinatorAgent
from agents.policy import PolicyAgent
from agents.reflection import ReflectionAgent
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore


LESSON = "Require target-version preflight before migration"


class LoopCoordinatorTests(unittest.TestCase):
    def build_agent(self, tmp):
        root = Path(tmp) / "data"
        state = Path(tmp) / "state"
        initialized = subprocess.run(
            [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(
            initialized.returncode,
            0,
            initialized.stdout + initialized.stderr,
        )
        candidates = CandidateStore(root, state)
        core = MemoryCore(MemoryStore(root), candidates)
        reflection = ReflectionAgent(candidates.state_dir)
        policy = PolicyAgent(root, candidates.state_dir)
        generator = LowRiskCandidateAgent(core, candidates.state_dir)
        return (
            LoopCoordinatorAgent(
                root,
                candidates.state_dir,
                reflection,
                policy,
                generator,
            ),
            root,
            candidates.state_dir,
        )

    def task(self, index=1, workspace="personal"):
        return {
            "type": "loop.coordinate",
            "confidentiality": "internal" if workspace == "work" else "personal",
            "input": {
                "task": f"Migration attempt {index}",
                "result": f"Migration {index} stopped before writes",
                "outcome": "fail",
                "error": "Target version mismatch",
                "reflection": "Validation stopped the unsafe migration",
                "root_cause": "The workflow omitted the target-version preflight",
                "next_change": LESSON,
                "workspace": workspace,
            },
        }

    def test_single_failed_loop_stops_at_review_with_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, root, _ = self.build_agent(tmp)
            task = self.task()
            result = agent.run(task, {})

        validate_result(result, agent, task)
        output = result["output"]
        self.assertEqual(output["reflection"]["reusable_lesson"], LESSON)
        self.assertEqual(output["policy"]["reviewable_count"], 1)
        self.assertEqual(len(output["candidate_generation"]), 1)
        evaluation = output["candidate_generation"][0]
        self.assertFalse(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "insufficient_independent_evidence")
        self.assertEqual(evaluation["independent_evidence_count"], 1)
        self.assertEqual(output["generated_candidate_ids"], [])
        self.assertFalse(output["applied"])
        self.assertFalse((root / "memory_rules.md").exists())

    def test_coordinator_preserves_session_candidate_workspace_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, root, _ = self.build_agent(tmp)
            work_task = self.task(workspace="work")
            personal_task = self.task(workspace="personal")
            work = agent.run(work_task, {})
            personal = agent.run(personal_task, {})
            store = MemoryStore(root)
            work_session = store.get(work["output"]["session_candidate_ids"][0])
            personal_session = store.get(personal["output"]["session_candidate_ids"][0])

        self.assertNotEqual(work["output"]["run_id"], personal["output"]["run_id"])
        self.assertEqual(work_session["workspace"], "work")
        self.assertEqual(work_session["confidentiality"], "internal")
        self.assertEqual(personal_session["workspace"], "personal")
        self.assertEqual(personal_session["confidentiality"], "personal")

    def test_three_independent_failures_create_one_review_candidate_and_reuse_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, root, _ = self.build_agent(tmp)
            first = agent.run(self.task(1), {})
            second = agent.run(self.task(2), {})
            third_task = self.task(3)
            third = agent.run(third_task, {})
            repeated = agent.run(third_task, {})

            candidate_id = third["output"]["generated_candidate_ids"][0]
            evaluation = third["output"]["candidate_generation"][0]
            candidate_path = root / evaluation["candidate_path"]
            candidate_text = candidate_path.read_text(encoding="utf-8")

        self.assertEqual(first["output"]["generated_candidate_ids"], [])
        self.assertEqual(second["output"]["generated_candidate_ids"], [])
        self.assertEqual(evaluation["independent_evidence_count"], 3)
        self.assertTrue(evaluation["eligible"])
        self.assertEqual(evaluation["reason"], "threshold_met")
        self.assertIn(candidate_id, third["candidates"])
        self.assertEqual(repeated["output"]["generated_candidate_ids"], [candidate_id])
        self.assertTrue(repeated["output"]["finalize_reused"])
        self.assertTrue(repeated["output"]["reflection"]["reused"])
        self.assertTrue(repeated["output"]["policy"]["reused"])
        self.assertTrue(repeated["output"]["candidate_generation"][0]["reused"])
        self.assertIn('status: "candidate"', candidate_text)
        self.assertFalse((root / "memory_rules.md").exists())

    def test_v09_cli_routes_coordinator_and_rejects_unknown_input_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, root, state = self.build_agent(tmp)
            task = self.task()
            cli = subprocess.run(
                [
                    sys.executable,
                    str(LAOS_CLI),
                    "--root",
                    str(root),
                    "--state-dir",
                    str(state),
                    "--task-json",
                    json.dumps(task, ensure_ascii=False, separators=(",", ":")),
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            payload = json.loads(cli.stdout)
            invalid = self.task()
            invalid["input"]["approve"] = True
            with self.assertRaises(ValueError):
                agent.run(invalid, {})

        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(payload["agent_id"], "loop_coordinator_agent")
        self.assertEqual(payload["task_type"], "loop.coordinate")
        self.assertEqual(payload["output"]["reflection"]["reusable_lesson"], LESSON)
        self.assertTrue(payload["requires_review"])


if __name__ == "__main__":
    unittest.main()
