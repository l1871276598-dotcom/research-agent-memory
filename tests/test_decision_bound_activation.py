import argparse
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


class DecisionBoundActivationTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def values(**overrides):
        values = {
            "type": "principle",
            "title": "Authority candidate",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "authority candidate content",
            "tags": ["authority"],
        }
        values.update(overrides)
        return values

    def create_candidate(self, **overrides):
        from memory.candidate import CandidateStore

        return CandidateStore(self.root, self.state).create(self.values(**overrides))

    def status(self, memory_id):
        from memory.store import MemoryStore

        return MemoryStore(self.root).get(memory_id)["status"]

    def test_review_publishes_decision_without_activation(self):
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)

        decision = gate.review("accept", created["candidate_id"])

        self.assertEqual(decision["status"], "decided")
        self.assertTrue(decision["decision_id"].startswith("mdec_"))
        self.assertEqual(decision["expected_active_generation"], 0)
        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        persisted = gate.authority_store.read_decision(decision["decision_id"])
        self.assertEqual(
            persisted["candidate_snapshot"]["artifact_sha256"],
            decision["candidate_artifact_sha256"],
        )

    def test_activation_requires_decision_and_is_idempotent(self):
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])

        first = gate.activate(
            decision["decision_id"], decision["expected_active_generation"]
        )
        second = gate.activate(
            decision["decision_id"], decision["expected_active_generation"]
        )

        self.assertEqual(self.status(created["candidate_id"]), "active")
        self.assertEqual(first["status"], "committed")
        self.assertEqual(first["activation_id"], second["activation_id"])
        self.assertEqual(first["active_generation"], 1)
        self.assertEqual(gate.authority_store.current_generation(), 1)

    def test_changed_candidate_is_stale_before_backend_mutation(self):
        from review import AuthorityError
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])
        path = self.root / created["path"]
        path.write_bytes(path.read_bytes() + b"\n")

        with self.assertRaises(AuthorityError) as raised:
            gate.activate(
                decision["decision_id"], decision["expected_active_generation"]
            )

        self.assertEqual(raised.exception.code, "stale_candidate")
        self.assertEqual(self.status(created["candidate_id"]), "candidate")

    def test_generation_cas_rejects_second_stale_decision(self):
        from review import AuthorityError
        from review.gate import ReviewGate

        gate = ReviewGate(self.root, self.state)
        first_candidate = self.create_candidate(title="First authority candidate")
        second_candidate = self.create_candidate(
            title="Second authority candidate",
            content="second authority candidate content",
        )
        first = gate.review("accept", first_candidate["candidate_id"])
        second = gate.review("accept", second_candidate["candidate_id"])
        self.assertEqual(first["expected_active_generation"], 0)
        self.assertEqual(second["expected_active_generation"], 0)

        gate.activate(first["decision_id"], 0)
        with self.assertRaises(AuthorityError) as raised:
            gate.activate(second["decision_id"], 0)

        self.assertEqual(raised.exception.code, "generation_conflict")
        self.assertEqual(self.status(second_candidate["candidate_id"]), "candidate")

    def test_tampered_bound_provenance_is_rejected_as_changed(self):
        import memory
        from review import AuthorityError
        from review.gate import ReviewGate

        created = self.create_candidate()
        path = self.root / created["path"]
        record, errors = memory.parse_front_matter(path)
        self.assertEqual(errors, [])
        record["source"] = "codex"
        record.pop("confirmation", None)
        record.pop("source_refs", None)
        record.pop("evidence", None)
        path.write_text(memory.render_existing_memory(path, record), encoding="utf-8")

        with self.assertRaises(AuthorityError) as raised:
            ReviewGate(self.root, self.state).review(
                "accept", created["candidate_id"]
            )

        self.assertEqual(raised.exception.code, "provenance_changed")
        self.assertEqual(self.status(created["candidate_id"]), "candidate")

    def test_unreceipted_postbaseline_active_memory_is_not_visible(self):
        import memory
        from memory.store import MemoryStore
        from review import AuthorityError, AuthorityMemoryStore, AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        record = {
            "id": "unreceipted-active",
            "type": "principle",
            "title": "Unreceipted",
            "created": "2026-08-05",
            "updated": "2026-08-05",
            "status": "active",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "test",
            "confidence": "confirmed",
            "content": "unreceipted active content",
            "tags": [],
        }
        path = self.root / memory.TYPE_DIRS["principle"] / "unreceipted-active-test.md"
        path.write_text(memory.render_memory(record), encoding="utf-8")
        guarded = AuthorityMemoryStore(MemoryStore(self.root), authority)

        with self.assertRaises(AuthorityError) as raised:
            guarded.active_relevant("unreceipted", "personal")

        self.assertEqual(raised.exception.code, "activation_receipt_missing")

    def test_receipted_activation_is_visible_to_search_and_context(self):
        from context.builder import ContextBuilder
        from memory.store import MemoryStore
        from review import AuthorityMemoryStore
        from review.gate import ReviewGate

        created = self.create_candidate(content="receipted context evidence")
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])
        activation = gate.activate(decision["decision_id"], 0)
        guarded = AuthorityMemoryStore(MemoryStore(self.root), gate.authority_store)

        rows = guarded.active_relevant("receipted context", "personal")
        pack = ContextBuilder(guarded).build(
            "receipted context", workspace="personal"
        )

        self.assertEqual([row["id"] for row in rows], [created["candidate_id"]])
        self.assertEqual(rows[0]["authority_receipt_id"], activation["activation_id"])
        self.assertEqual(pack["sources"], [created["candidate_id"]])


if __name__ == "__main__":
    unittest.main()
