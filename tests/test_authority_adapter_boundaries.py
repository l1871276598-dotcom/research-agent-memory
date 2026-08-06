import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory
import memory_tools
import agents.candidate_generator as candidate_generator
from agents.candidate_generator import LowRiskCandidateAgent
from agents.coordinator import LoopCoordinatorAgent
from agents.orchestrator import (
    ActivationAgent,
    ImportAgent,
    MemoryAgent,
    ReviewAgent,
)
from agents.policy import PolicyAgent
from agents.reflection import ConversationReviewAgent
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore
from reflection.conversation_review import ConversationReviewService
from reflection.coordinator import ConversationReviewCoordinator
from review import AuthorityStore


class AuthorityAdapterBoundaryTests(unittest.TestCase):
    """Focused Step 3/4 adapter evidence in the authorized order."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "data"
        self.state = self.base / "state"
        memory.init_store(self.root)
        memory.db_init(
            argparse.Namespace(
                root=str(self.root),
                state_dir=str(self.state),
            )
        )
        self.authority = AuthorityStore(self.root, self.state)
        self.core = MemoryCore(
            MemoryStore(self.root),
            CandidateStore(self.root, self.state),
        )

    def tearDown(self):
        self.temp.cleanup()

    def authority_snapshot(self):
        return {
            "generation": self.authority.current_generation(),
            "decisions": sorted(
                path.name
                for path in self.authority.decisions.iterdir()
                if not path.name.startswith(".tmp.")
            ),
            "activations": sorted(
                path.name
                for path in self.authority.activations.iterdir()
                if not path.name.startswith(".tmp.")
            ),
            "pending": self.authority.pending_count(),
        }

    def assert_candidate_only(self, before, candidate_id):
        self.assertEqual(self.authority_snapshot(), before)
        record = MemoryStore(self.root).get(candidate_id)
        self.assertEqual(record["status"], "candidate")
        self.assertEqual(self.authority.current_generation(), 0)

    def test_01_f001_memory_agent_creates_candidate_without_authority_effect(self):
        agent = MemoryAgent(self.core)
        before = self.authority_snapshot()
        result = agent.run(
            {
                "type": "memory.create",
                "workspace": "personal",
                "input": {
                    "type": "principle",
                    "title": "F-001 candidate boundary",
                    "scope": "global",
                    "confidentiality": "personal",
                    "source": "manual:user_confirmed",
                    "confidence": "confirmed",
                    "content": "F-001 must stop at candidate state.",
                    "tags": ["authority-boundary"],
                },
            },
            {},
        )

        candidate_id = result["output"]["candidate_id"]
        self.assertEqual(result["candidates"], [candidate_id])
        self.assertTrue(result["requires_review"])
        self.assert_candidate_only(before, candidate_id)
        self.assertFalse(callable(getattr(agent, "activate", None)))

    def test_02_f005_import_agent_archives_untrusted_input_without_authority_effect(self):
        source = self.base / "untrusted-import.txt"
        source.write_text("untrusted imported bytes", encoding="utf-8")
        agent = ImportAgent(self.root, memory_tools)
        before = self.authority_snapshot()
        result = agent.run(
            {
                "type": "import.file",
                "input": {"path": str(source)},
            },
            {},
        )

        self.assertEqual(result["agent_id"], "import_agent")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(self.authority_snapshot(), before)
        self.assertEqual(
            [row for row in MemoryStore(self.root).records() if row["status"] == "active"],
            [],
        )
        self.assertFalse(callable(getattr(agent, "activate", None)))

    def test_03_w016_conversation_review_apply_creates_candidate_only(self):
        service = ConversationReviewService(self.core, state_dir=self.state)
        before = self.authority_snapshot()
        result = service.apply(
            {
                "memory_candidates": [
                    {
                        "type": "profile",
                        "title": "W-016 review candidate",
                        "scope": "global",
                        "content": "Conversation review may only propose memory.",
                        "tags": ["conversation-review"],
                    }
                ],
                "skill_candidates": [],
                "nothing_to_save": False,
            },
            workspace="personal",
            confidentiality="personal",
            session_id="session-w016",
            review_id="review-w016",
        )

        candidate_id = result["candidate_ids"][0]
        self.assert_candidate_only(before, candidate_id)
        record = MemoryStore(self.root).get(candidate_id)
        self.assertEqual(record["source"], "agent:conversation_review")
        self.assertEqual(record["source_provenance"], "verified")
        self.assertEqual(len(record["source_refs"]), 1)
        self.assertTrue(record["source_refs"][0].startswith("artifact:"))
        self.assertFalse(callable(getattr(service, "activate", None)))

    def test_04_w013_low_risk_generator_creates_review_candidate_only(self):
        lesson = "require: verify immutable target before applying change"
        policy_id = hashlib.sha256(lesson.encode("utf-8")).hexdigest()[:16]
        evidence = [
            {
                "run_id": f"{index:032x}",
                "evidence_fingerprint": f"{index:064x}",
                "outcome": "pass",
            }
            for index in range(1, 4)
        ]
        for item in evidence:
            run_dir = (
                self.state
                / "loop_engineering"
                / "runs"
                / item["run_id"]
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": item["run_id"],
                        "workspace": "personal",
                        "project": None,
                        "outcome": "pass",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        agent = LowRiskCandidateAgent(self.core, self.state)
        before = self.authority_snapshot()

        with mock.patch.object(
            candidate_generator,
            "_collect_evidence",
            return_value=(lesson, evidence, []),
        ):
            result = agent.run(
                {
                    "type": "loop.generate-candidate",
                    "input": {
                        "policy_id": policy_id,
                        "workspace": "personal",
                    },
                },
                {},
            )

        candidate_id = result["output"]["candidate_id"]
        self.assertTrue(result["output"]["eligible"])
        self.assertFalse(result["output"]["applied"])
        self.assertEqual(result["candidates"], [candidate_id])
        self.assert_candidate_only(before, candidate_id)
        self.assertFalse(callable(getattr(agent, "activate", None)))

    def test_05_non_memory_artifact_producers_remain_stop(self):
        registry = json.loads(
            (SOURCE_DIR / "agents/registry-v0.9.yaml").read_text(encoding="utf-8")
        )
        activation_owners = [
            entry["id"]
            for entry in registry["agents"]
            if "memory.activate" in entry["handles"]
        ]

        self.assertEqual(activation_owners, ["activation_agent"])
        self.assertEqual(ReviewAgent.handles, ["memory.review"])
        self.assertEqual(ActivationAgent.handles, ["memory.activate"])
        self.assertNotIn("memory.activate", LoopCoordinatorAgent.handles)
        self.assertNotIn("memory.activate", PolicyAgent.handles)
        self.assertNotIn("memory.activate", ConversationReviewAgent.handles)
        self.assertFalse(hasattr(ConversationReviewCoordinator, "activate"))
        self.assertFalse(hasattr(LoopCoordinatorAgent, "activate"))
        self.assertFalse(hasattr(PolicyAgent, "activate"))


if __name__ == "__main__":
    unittest.main()
