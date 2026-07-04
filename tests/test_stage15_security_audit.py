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
import agents.candidate_generator as candidate_generator
from agents.candidate_generator import LowRiskCandidateAgent
from agents.policy import PolicyAgent
from agents.reflection import ReflectionAgent
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore


class Stage15SecurityAuditTests(unittest.TestCase):
    def init_store(self, tmp):
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
        return root, state

    @staticmethod
    def agent(root, state):
        candidates = CandidateStore(root, state)
        return LowRiskCandidateAgent(
            MemoryCore(MemoryStore(root), candidates),
            candidates.state_dir,
        )

    def create_policy_evidence(
        self,
        root,
        state,
        index,
        lesson,
        workspace,
        project=None,
        result=None,
    ):
        finalized = memory_agent.finalize_memory(
            root,
            f"Partitioned task {index}",
            f"Partitioned result {index}" if result is None else result,
            state_dir=state,
            outcome="pass",
            reflection="The partitioned task completed",
            next_change=lesson,
            workspace=workspace,
            project=project,
        )
        run_id = finalized["loop_run"]["run_id"]
        ReflectionAgent(state).run(
            {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
        )
        policy = PolicyAgent(root, state).run(
            {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
        )
        return policy["output"]["artifact"], run_id

    def test_work_evidence_cannot_generate_personal_principle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            lesson = "Require partition-preserving candidate generation"
            policy_id = memory_agent._policy_id(lesson)
            for index in range(3):
                self.create_policy_evidence(
                    root, state, index, lesson, workspace="work"
                )
            agent = self.agent(root, state)
            personal = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {"policy_id": policy_id, "workspace": "personal"},
                },
                {},
            )
            work = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {"policy_id": policy_id, "workspace": "work"},
                },
                {},
            )
            candidate = MemoryStore(root).get(work["output"]["candidate_id"])

        self.assertFalse(personal["output"]["eligible"])
        self.assertEqual(personal["output"]["independent_evidence_count"], 0)
        self.assertTrue(work["output"]["eligible"])
        self.assertEqual(candidate["workspace"], "work")
        self.assertEqual(candidate["confidentiality"], "internal")

    def test_project_evidence_is_counted_only_inside_the_same_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            lesson = "Require project-local evidence aggregation"
            policy_id = memory_agent._policy_id(lesson)
            for project in ("project-a", "project-b"):
                for index in range(3):
                    self.create_policy_evidence(
                        root,
                        state,
                        index,
                        lesson,
                        workspace="work",
                        project=project,
                        result="",
                    )
            _, a_evidence, a_blockers = candidate_generator._collect_evidence(
                state, policy_id, "work", "project-a"
            )
            _, b_evidence, b_blockers = candidate_generator._collect_evidence(
                state, policy_id, "work", "project-b"
            )
            _, global_evidence, global_blockers = candidate_generator._collect_evidence(
                state, policy_id, "work", None
            )

        self.assertEqual(len(a_evidence), 3)
        self.assertEqual(len(b_evidence), 3)
        self.assertEqual(global_evidence, [])
        self.assertEqual(a_blockers, [])
        self.assertEqual(b_blockers, [])
        self.assertEqual(global_blockers, [])

    def test_policy_agent_rejects_structurally_coordinated_reflection_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            _, run_id = self.create_policy_evidence(
                root,
                state,
                1,
                "Require deterministic reflection verification",
                workspace="personal",
            )
            run_dir = state / "loop_engineering" / "runs" / run_id
            (run_dir / "policy_candidates.json").unlink()
            (run_dir / "policy_review.md").unlink()
            reflection_path = run_dir / "reflection_result.json"
            reflection = json.loads(reflection_path.read_text(encoding="utf-8"))
            reflection["reflection"]["likely_cause"] = "forged cause"
            reflection["reflection"]["unknown_fields"] = [
                item
                for item in reflection["reflection"]["unknown_fields"]
                if item != "likely_cause"
            ]
            reflection_path.write_text(
                json.dumps(reflection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                PolicyAgent(root, state).run(
                    {"type": "loop.suggest-policies", "input": {"run_id": run_id}},
                    {},
                )

        self.assertFalse((run_dir / "policy_candidates.json").exists())
        self.assertFalse((run_dir / "policy_review.md").exists())

    def test_partitioned_v2_run_requires_complete_known_partition_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_store(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "Partitioned task",
                "Partitioned result",
                state_dir=state,
                outcome="pass",
                reflection="Completed",
                workspace="work",
            )
            run_id = finalized["loop_run"]["run_id"]
            run_path = state / finalized["loop_run"]["path"] / "run.json"
            original = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(original["workspace"], "work")
            self.assertIsNone(original["project"])

            missing_project = dict(original)
            missing_project.pop("project")
            run_path.write_text(
                json.dumps(missing_project, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(memory_agent.AgentError):
                memory_agent._load_loop_run(state, run_id)

            unknown = dict(original)
            unknown["unexpected"] = True
            run_path.write_text(
                json.dumps(unknown, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(memory_agent.AgentError):
                memory_agent._load_loop_run(state, run_id)


if __name__ == "__main__":
    unittest.main()
