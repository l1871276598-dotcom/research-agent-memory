import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from procedures.manager import ProcedureManager
from procedures.proposals import ProcedureProposalStore


def proposal(action="create", **extra):
    value = {
        "action": action,
        "name": "research-workflow",
        "summary": "Evidence-first research workflow",
        "content": "Verify evidence before promoting durable rules.",
        "source_refs": ["session:test"],
    }
    value.update(extra)
    return value


class ProcedureManagerTests(unittest.TestCase):
    def test_candidate_requires_acceptance_then_can_apply_and_rollback(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            proposals = ProcedureProposalStore(state)
            manager = ProcedureManager(data, proposals)
            created = proposals.create(proposal())

            with self.assertRaises(ValueError):
                manager.apply(created["proposal_id"])

            proposals.review(created["proposal_id"], "accept", "verified")
            applied = manager.apply(created["proposal_id"])
            path = Path(data) / "procedures" / applied["path"]
            self.assertTrue(path.is_file())
            self.assertIn("Verify evidence", path.read_text(encoding="utf-8"))
            self.assertEqual(proposals.get(created["proposal_id"])["status"], "applied")

            rolled_back = manager.rollback(created["proposal_id"])
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertFalse(path.exists())

    def test_update_requires_exact_pre_review_hash_and_restores_previous_content(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            proposals = ProcedureProposalStore(state)
            manager = ProcedureManager(data, proposals)
            first = proposals.create(proposal())
            proposals.review(first["proposal_id"], "accept")
            created = manager.apply(first["proposal_id"])
            path = Path(data) / "procedures" / created["path"]
            original = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()

            update = proposals.create(
                proposal(
                    "update",
                    content="Verify evidence and record provenance before promotion.",
                    expected_sha256=digest,
                )
            )
            proposals.review(update["proposal_id"], "accept")
            manager.apply(update["proposal_id"])
            self.assertIn("record provenance", path.read_text(encoding="utf-8"))

            manager.rollback(update["proposal_id"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_stale_update_and_post_apply_changes_fail_closed(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            proposals = ProcedureProposalStore(state)
            manager = ProcedureManager(data, proposals)
            create = proposals.create(proposal())
            proposals.review(create["proposal_id"], "accept")
            applied = manager.apply(create["proposal_id"])
            path = Path(data) / "procedures" / applied["path"]

            update = proposals.create(proposal("update", expected_sha256="0" * 64))
            proposals.review(update["proposal_id"], "accept")
            with self.assertRaisesRegex(ValueError, "changed since proposal review"):
                manager.apply(update["proposal_id"])

            path.write_text("changed after application", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after application"):
                manager.rollback(create["proposal_id"])


if __name__ == "__main__":
    unittest.main()
