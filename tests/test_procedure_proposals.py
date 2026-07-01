import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from procedures.proposals import ProcedureProposalStore, validate_proposal
from reflection import ConversationReviewCoordinator, ReviewStateStore


class Service:
    def review(self, messages, **kwargs):
        return {
            "candidate_ids": [],
            "skill_candidates": [
                {
                    "name": "research-workflow",
                    "change": "Verify evidence before promoting a durable rule.",
                }
            ],
            "nothing_to_save": False,
        }


class ProcedureProposalTests(unittest.TestCase):
    def test_store_requires_evidence_and_keeps_candidate_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProcedureProposalStore(directory)
            value = {
                "action": "update",
                "name": "research-workflow",
                "summary": "Add verification gate",
                "content": "Verify evidence before promotion.",
                "source_refs": ["session:one"],
            }
            created = store.create(value)
            loaded = store.get(created["proposal_id"])
            self.assertEqual(loaded["status"], "candidate")
            self.assertEqual(loaded["proposal"], value)
            self.assertEqual(len(store.list()), 1)

            invalid = dict(value, source_refs=[])
            with self.assertRaises(ValueError):
                validate_proposal(invalid)

    def test_review_key_returns_existing_proposal_on_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProcedureProposalStore(directory)
            value = {
                "action": "update",
                "name": "research-workflow",
                "summary": "Add verification gate",
                "content": "Verify evidence before promotion.",
                "source_refs": ["session:one"],
                "review_key": "review:event-1:procedure:0",
            }
            first = store.create(value)
            replay = store.create(value)
            self.assertEqual(first["proposal_id"], replay["proposal_id"])
            self.assertEqual(len(store.list()), 1)

    def test_coordinator_converts_review_output_to_procedure_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            proposals = ProcedureProposalStore(directory)
            coordinator = ConversationReviewCoordinator(
                Service(),
                ReviewStateStore(directory),
                procedure_proposals=proposals,
                memory_interval=100,
                skill_interval=1,
            )
            result = coordinator.record_turn(
                session_id="session-one",
                messages=[{"role": "user", "content": "Use verification gates."}],
                workspace="personal",
                tool_iterations=1,
                event_key="bridge-event-one",
                review_position=0,
            )
            self.assertEqual(result["status"], "reviewed")
            saved = result["result"]["procedure_proposals"]
            self.assertEqual(len(saved), 1)
            proposal = saved[0]["proposal"]
            self.assertEqual(proposal["action"], "update")
            self.assertEqual(proposal["source_refs"], ["session:session-one"])
            self.assertEqual(
                proposal["review_key"],
                "review:bridge-event-one:procedure:0",
            )
            self.assertIn("Verify evidence", proposal["content"])


if __name__ == "__main__":
    unittest.main()
