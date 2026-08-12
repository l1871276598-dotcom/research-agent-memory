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
import agents.candidate_generator as candidate_generator
from agents.base import validate_result
from agents.candidate_generator import LowRiskCandidateAgent
from agents.policy import PolicyAgent
from agents.reflection import ReflectionAgent
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore


class LowRiskCandidateAgentTests(unittest.TestCase):
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

    def core(self, root, state):
        return MemoryCore(MemoryStore(root), CandidateStore(root, state))

    def create_evidence_run(
        self,
        root,
        state,
        index,
        lesson,
        task=None,
        result=None,
        extra=None,
        workspace=None,
        project=None,
    ):
        task = task or f"Validate migration case {index}"
        result = result or f"Migration case {index} completed"
        finalized = memory_agent.finalize_memory(
            root,
            task,
            result,
            state_dir=state,
            outcome="pass",
            reflection=f"Version preflight passed for case {index}",
            policy_suggestions=[extra] if extra else None,
            workspace=workspace,
            project=project,
        )
        run_id = finalized["loop_run"]["run_id"]
        run_dir = state / finalized["loop_run"]["path"]
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
        PolicyAgent(root, state).run(
            {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
        )
        return run_id

    @staticmethod
    def policy_id(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def principle_candidates(root):
        return [
            item
            for item in MemoryStore(root).records()
            if item["type"] == "principle" and item["status"] == "candidate"
        ]

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

    def test_less_than_three_independent_runs_never_creates_principle_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            for index in range(2):
                self.create_evidence_run(root, state, index, lesson)
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            task = {
                "type": "loop.generate-candidate",
                "input": {"policy_id": self.policy_id(lesson), "workspace": "personal"},
            }
            result = agent.run(task, {})
            principles = self.principle_candidates(root)
            generated_dir = state / "loop_engineering" / "generated_candidates"
            artifacts = list(generated_dir.glob("*.json")) if generated_dir.exists() else []
        validate_result(result, agent, task)
        self.assertFalse(result["output"]["eligible"])
        self.assertEqual(result["output"]["reason"], "insufficient_independent_evidence")
        self.assertEqual(result["output"]["independent_evidence_count"], 2)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(principles, [])
        self.assertEqual(artifacts, [])

    def test_no_loop_runs_is_normal_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            result = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {
                        "policy_id": self.policy_id(lesson),
                        "workspace": "personal",
                    },
                },
                {},
            )
        self.assertFalse(result["output"]["eligible"])
        self.assertEqual(result["output"]["reason"], "insufficient_independent_evidence")
        self.assertEqual(result["output"]["independent_evidence_count"], 0)

    def test_same_task_result_fingerprint_is_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            for index in range(3):
                self.create_evidence_run(
                    root,
                    state,
                    index,
                    lesson,
                    task="Same task",
                    result="Same result",
                    extra=f"legacy-{index}",
                )
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            result = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {"policy_id": self.policy_id(lesson), "workspace": "personal"},
                },
                {},
            )
            principles = self.principle_candidates(root)
        self.assertFalse(result["output"]["eligible"])
        self.assertEqual(result["output"]["independent_evidence_count"], 1)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(principles, [])

    def test_three_independent_runs_create_one_review_candidate_and_reuse_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            run_ids = [self.create_evidence_run(root, state, i, lesson) for i in range(3)]
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            task = {
                "type": "loop.generate-candidate",
                "input": {"policy_id": self.policy_id(lesson), "workspace": "personal"},
            }
            first = agent.run(task, {})
            second = agent.run(task, {})
            candidate = MemoryStore(root).get(first["output"]["candidate_id"])
            artifact_path = next(
                (state / "loop_engineering" / "generated_candidates").glob("*.json")
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            active = [item for item in MemoryStore(root).records() if item["status"] == "active"]
            principles = self.principle_candidates(root)
        validate_result(first, agent, task)
        self.assertTrue(first["output"]["eligible"])
        self.assertFalse(first["output"]["reused"])
        self.assertTrue(second["output"]["reused"])
        self.assertEqual(first["output"]["candidate_id"], second["output"]["candidate_id"])
        self.assertEqual(first["candidates"], [first["output"]["candidate_id"]])
        self.assertEqual(len(principles), 1)
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["type"], "principle")
        self.assertEqual(candidate["workspace"], "personal")
        self.assertEqual(candidate["confidentiality"], "personal")
        self.assertEqual(candidate["confidence"], "inferred")
        self.assertEqual(candidate["source"], "loop-engineering:stage13")
        self.assertEqual(set(candidate["evidence"]), {f"loop-run:{rid}" for rid in run_ids})
        self.assertIn("requires Review Gate approval", candidate["content"])
        self.assertEqual(artifact["status"], "candidate")
        self.assertEqual(artifact["candidate_id"], candidate["id"])
        self.assertEqual(len(artifact["evidence_fingerprints"]), 3)
        self.assertFalse(artifact["applied"])
        self.assertEqual(active, [])

    def test_pending_intent_recovers_same_candidate_after_interrupted_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            for index in range(3):
                self.create_evidence_run(root, state, index, lesson)
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            task = {
                "type": "loop.generate-candidate",
                "input": {"policy_id": self.policy_id(lesson), "workspace": "personal"},
            }
            first = agent.run(task, {})
            artifact_path = next(
                (state / "loop_engineering" / "generated_candidates").glob("*.json")
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact["candidate_id"] = None
            artifact["candidate_path"] = None
            artifact["status"] = "pending_creation"
            artifact_path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            recovered = agent.run(task, {})
            records = [
                item
                for item in MemoryStore(root).records()
                if f"loop-candidate-request:{first['output']['request_id']}"
                in item.get("source_refs", [])
            ]
        self.assertEqual(recovered["output"]["candidate_id"], first["output"]["candidate_id"])
        self.assertTrue(recovered["output"]["reused"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "candidate")

    def test_forged_pending_intent_cannot_bypass_evidence_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            for index in range(3):
                self.create_evidence_run(root, state, index, lesson)
            policy_id = self.policy_id(lesson)
            request, request_id = candidate_generator._request(
                policy_id, "personal", None
            )
            policy_text, evidence, blockers = candidate_generator._collect_evidence(
                state, policy_id
            )
            self.assertEqual(blockers, [])
            intent = candidate_generator._new_intent(
                request, request_id, policy_text, evidence
            )
            intent["evidence_run_ids"][0] = "f" * 32
            intent["evidence_fingerprints"][0] = "e" * 64
            artifact_path = (
                candidate_generator._generation_dir(state) / f"{request_id}.json"
            )
            artifact_path.write_text(
                json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            with self.assertRaises(ValueError):
                agent.run(
                    {
                        "type": "loop.generate-candidate",
                        "input": {"policy_id": policy_id, "workspace": "personal"},
                    },
                    {},
                )
            principles = self.principle_candidates(root)
        self.assertEqual(principles, [])

    def test_coordinated_reflection_and_policy_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            run_ids = [self.create_evidence_run(root, state, i, lesson) for i in range(3)]
            run_dir = state / "loop_engineering" / "runs" / run_ids[0]
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
            policy_path = run_dir / "policy_candidates.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["source_reflection_artifact_sha256"] = hashlib.sha256(
                reflection_path.read_bytes()
            ).hexdigest()
            policy_path.write_text(
                json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            with self.assertRaises(ValueError):
                agent.run(
                    {
                        "type": "loop.generate-candidate",
                        "input": {
                            "policy_id": self.policy_id(lesson),
                            "workspace": "personal",
                        },
                    },
                    {},
                )
            principles = self.principle_candidates(root)
        self.assertEqual(principles, [])

    def test_duplicate_policy_blocks_principle_candidate_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "Keep the version preflight"
            (root / "memory_rules.md").write_text(self.rule_text(lesson), encoding="utf-8")
            for index in range(3):
                self.create_evidence_run(root, state, index, lesson)
            agent = LowRiskCandidateAgent(self.core(root, state), state)
            result = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {"policy_id": self.policy_id(lesson), "workspace": "personal"},
                },
                {},
            )
            principles = self.principle_candidates(root)
        self.assertFalse(result["output"]["eligible"])
        self.assertEqual(result["output"]["reason"], "policy_not_reviewable")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(principles, [])

    def test_cli_routes_stage13_and_work_candidate_remains_in_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            lesson = "require: validate target before migration"
            for index in range(3):
                self.create_evidence_run(
                    root,
                    state,
                    index,
                    lesson,
                    workspace="work",
                )
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
                            "type": "loop.generate-candidate",
                            "confidentiality": "internal",
                            "input": {
                                "policy_id": self.policy_id(lesson),
                                "workspace": "work",
                            },
                        },
                        separators=(",", ":"),
                    ),
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            payload = json.loads(cli.stdout)
            candidate = MemoryStore(root).get(payload["output"]["candidate_id"])
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(payload["agent_id"], "low_risk_candidate_agent")
        self.assertEqual(payload["task_type"], "loop.generate-candidate")
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["workspace"], "work")
        self.assertEqual(candidate["confidentiality"], "internal")
        self.assertEqual(payload["candidates"], [candidate["id"]])
        self.assertTrue(payload["requires_review"])
        self.assertFalse(payload["output"]["applied"])


if __name__ == "__main__":
    unittest.main()
