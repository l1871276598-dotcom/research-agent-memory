import argparse
import hashlib
import json
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

    def write_active_memory(self, memory_id, memory_type, title, scope, content, **fields):
        import memory

        record = {
            "id": memory_id,
            "type": memory_type,
            "title": title,
            "created": "2026-08-10",
            "updated": "2026-08-10",
            "status": "active",
            "scope": scope,
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "test",
            "confidence": "confirmed",
            "content": content,
            "tags": [],
            **fields,
        }
        path = self.root / memory.TYPE_DIRS[memory_type] / f"{memory_id}-fixture.md"
        path.write_text(memory.render_memory(record), encoding="utf-8")
        return path

    def create_target_mutation(self):
        self.write_active_memory(
            "project-a", "project", "Project A", "project", "project a", project="project-a"
        )
        self.write_active_memory(
            "context-a", "context", "Context A", "context", "context a", context_id="context-a"
        )
        target_path = self.write_active_memory(
            "target-memory",
            "principle",
            "Target memory",
            "project",
            "target content",
            project="project-a",
            context_id="context-a",
        )
        created = self.create_candidate(
            action="merge",
            title="Target mutation candidate",
            scope="project",
            project="project-a",
            context_id="context-a",
            target_id="target-memory",
        )
        return created, target_path

    def status(self, memory_id):
        from memory.store import MemoryStore

        return MemoryStore(self.root).get(memory_id)["status"]

    def write_pending(
        self,
        gate,
        decision,
        *,
        active_before,
        include_digest=True,
        digest=None,
        extra=None,
    ):
        pending = {
            "schema_version": 1,
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
            "candidate_before": gate.authority_store.read_decision(
                decision["decision_id"]
            )["candidate_snapshot"],
            "previous_generation": decision["expected_active_generation"],
            "active_before": active_before,
            "prepared_at": "2026-08-11T00:00:00Z",
        }
        if extra:
            pending.update(extra)
        if include_digest:
            pending["pending_sha256"] = (
                digest
                if digest is not None
                else hashlib.sha256(
                    json.dumps(
                        pending,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            )
        (gate.authority_store.pending / f"{decision['decision_id']}.json").write_text(
            json.dumps(
                pending,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def prepare_unrelated_active_pending(self, *, include_digest=True, digest=None):
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])
        self.write_active_memory(
            "unrelated-active",
            "principle",
            "Unrelated active",
            "global",
            "unrelated active content",
        )
        self.write_pending(
            gate,
            decision,
            active_before={},
            include_digest=include_digest,
            digest=digest,
        )
        return created, gate, decision

    def assert_pending_rejected(self, gate, decision):
        from review import AuthorityError

        try:
            activation = gate.activate(
                decision["decision_id"], decision["expected_active_generation"]
            )
        except AuthorityError as exc:
            self.assertEqual(exc.code, "pending_invalid")
            return
        self.fail(f"forged pending was accepted: {activation['authorized_records']!r}")

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

    def test_pending_without_digest_is_rejected_before_activation(self):
        created, gate, decision = self.prepare_unrelated_active_pending(
            include_digest=False
        )

        self.assert_pending_rejected(gate, decision)

        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)
        self.assertFalse(
            (gate.authority_store.activations / f"{decision['decision_id']}.json").exists()
        )

    def test_invalid_pending_digest_is_rejected_before_activation(self):
        created, gate, decision = self.prepare_unrelated_active_pending(
            digest="0" * 64
        )

        self.assert_pending_rejected(gate, decision)

        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)

    def test_recomputed_pending_digest_cannot_authorize_unrelated_active_memory(self):
        created, gate, decision = self.prepare_unrelated_active_pending()

        self.assert_pending_rejected(gate, decision)

        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)

    def test_pending_with_extra_key_is_rejected_before_activation(self):
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])
        self.write_pending(
            gate,
            decision,
            active_before={},
            extra={"unexpected": "forged"},
        )

        self.assert_pending_rejected(gate, decision)

        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)

    def test_exact_pending_recovers_after_backend_crash(self):
        from review.gate import ReviewGate

        created = self.create_candidate(content="crash recovery authority evidence")
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])

        def mutate_then_crash(decision_record):
            gate.backend._accept_candidate_impl(
                argparse.Namespace(
                    root=str(self.root),
                    state_dir=str(self.state),
                    id=decision_record["candidate_snapshot"]["candidate_id"],
                ),
                authority=gate._authority,
            )
            raise RuntimeError("injected crash after backend mutation")

        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            gate.authority_store.activate(
                decision["decision_id"],
                decision["expected_active_generation"],
                mutate_then_crash,
            )

        recovered = gate.activate(
            decision["decision_id"], decision["expected_active_generation"]
        )

        self.assertEqual(recovered["status"], "committed")
        self.assertEqual(recovered["backend_status"], "recovered")
        self.assertEqual(recovered["authorized_records"].keys(), {created["candidate_id"]})
        self.assertEqual(gate.authority_store.current_generation(), 1)

    def test_recovery_rejects_pending_that_omits_reviewed_active_target(self):
        from review import AuthorityError
        from review.gate import ReviewGate

        self.write_active_memory(
            "target-memory",
            "principle",
            "Target memory",
            "global",
            "target content",
        )
        created = self.create_candidate(
            action="UPDATE",
            target_id="target-memory",
            title="Replacement candidate",
            content="replacement content",
        )
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])

        def mutate_then_crash(decision_record):
            gate.backend._accept_candidate_impl(
                argparse.Namespace(
                    root=str(self.root),
                    state_dir=str(self.state),
                    id=decision_record["candidate_snapshot"]["candidate_id"],
                ),
                authority=gate._authority,
            )
            raise RuntimeError("injected crash after backend mutation")

        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            gate.authority_store.activate(
                decision["decision_id"],
                decision["expected_active_generation"],
                mutate_then_crash,
            )

        pending_path = gate.authority_store.pending / f"{decision['decision_id']}.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        self.assertIn("target-memory", pending["active_before"])
        pending["active_before"].pop("target-memory")
        body = dict(pending)
        body.pop("pending_sha256")
        pending["pending_sha256"] = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        pending_path.write_text(
            json.dumps(
                pending,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        try:
            recovered = gate.activate(
                decision["decision_id"], decision["expected_active_generation"]
            )
        except AuthorityError as exc:
            self.assertEqual(exc.code, "outcome_uncertain")
        else:
            self.fail(
                "recovery accepted a pending baseline without its reviewed target: "
                f"{recovered['authorized_records']!r}"
            )

    def test_unexpected_active_binding_is_not_authorized_by_receipt(self):
        from review import AuthorityError
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])

        def activate_candidate_and_mutate_unrelated(decision_record):
            result = gate.backend._accept_candidate_impl(
                argparse.Namespace(
                    root=str(self.root),
                    state_dir=str(self.state),
                    id=decision_record["candidate_snapshot"]["candidate_id"],
                ),
                authority=gate._authority,
            )
            self.write_active_memory(
                "unexpected-active",
                "principle",
                "Unexpected active",
                "global",
                "unexpected active content",
            )
            return result

        try:
            receipt, _ = gate.authority_store.activate(
                decision["decision_id"],
                decision["expected_active_generation"],
                activate_candidate_and_mutate_unrelated,
            )
        except AuthorityError as exc:
            self.assertEqual(exc.code, "outcome_uncertain")
        else:
            self.fail(
                "unexpected active binding was receipted: "
                f"{receipt['authorized_records']!r}"
            )

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

    def test_target_binding_is_transitive_in_decision_and_activation_receipt(self):
        from review.gate import ReviewGate

        created, target_path = self.create_target_mutation()
        gate = ReviewGate(self.root, self.state)

        decision = gate.review("merge", created["candidate_id"])

        binding = gate.authority_store.read_decision(decision["decision_id"])[
            "candidate_snapshot"
        ]["target_binding"]
        self.assertEqual(
            binding,
            {
                "target_id": "target-memory",
                "relative_path": target_path.relative_to(self.root).as_posix(),
                "artifact_sha256": hashlib.sha256(target_path.read_bytes()).hexdigest(),
                "partition": {
                    "workspace": "personal",
                    "project": "project-a",
                    "context_id": "context-a",
                    "confidentiality": "personal",
                },
            },
        )

        gate.activate(decision["decision_id"], decision["expected_active_generation"])

        receipt = json.loads(
            (gate.authority_store.activations / f"{decision['decision_id']}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(receipt["candidate_before"]["target_binding"], binding)
        self.assertEqual(
            receipt["candidate_after"]["target_binding"]["target_id"], "target-memory"
        )
        self.assertNotEqual(
            receipt["candidate_after"]["target_binding"]["artifact_sha256"],
            binding["artifact_sha256"],
        )

    def test_target_artifact_drift_is_rejected_before_activation_mutation(self):
        from review import AuthorityError
        from review.gate import ReviewGate

        created, target_path = self.create_target_mutation()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("merge", created["candidate_id"])
        target_path.write_text(
            target_path.read_text(encoding="utf-8").replace(
                "target content", "changed target content", 1
            ),
            encoding="utf-8",
        )

        with self.assertRaises(AuthorityError) as raised:
            gate.activate(decision["decision_id"], decision["expected_active_generation"])

        self.assertEqual(raised.exception.code, "stale_candidate")
        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)
        self.assertFalse(
            (gate.authority_store.activations / f"{decision['decision_id']}.json").exists()
        )

    def test_target_partition_drift_is_rejected_before_activation_mutation(self):
        import memory
        from review import AuthorityError
        from review.gate import ReviewGate

        created, target_path = self.create_target_mutation()
        self.write_active_memory(
            "project-b", "project", "Project B", "project", "project b", project="project-b"
        )
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("merge", created["candidate_id"])
        target, errors = memory.parse_front_matter(target_path)
        self.assertEqual(errors, [])
        target["project"] = "project-b"
        target_path.write_text(
            memory.render_existing_memory(target_path, target), encoding="utf-8"
        )

        with self.assertRaises(AuthorityError) as raised:
            gate.activate(decision["decision_id"], decision["expected_active_generation"])

        self.assertEqual(raised.exception.code, "stale_candidate")
        self.assertEqual(self.status(created["candidate_id"]), "candidate")
        self.assertEqual(gate.authority_store.current_generation(), 0)
        self.assertFalse(
            (gate.authority_store.activations / f"{decision['decision_id']}.json").exists()
        )

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
