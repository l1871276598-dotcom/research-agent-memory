import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


class ReviewAuthorityScopeAndSourceTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(
            argparse.Namespace(root=str(self.root), state_dir=str(self.state))
        )

    @staticmethod
    def candidate_values(**overrides):
        values = {
            "type": "principle",
            "title": "Scoped review candidate",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "scoped review candidate content",
            "tags": ["review-scope"],
        }
        values.update(overrides)
        return values

    def create_candidate(self, **overrides):
        from memory.candidate import CandidateStore

        return CandidateStore(self.root, self.state).create(
            self.candidate_values(**overrides)
        )

    def test_publish_requires_workspace_and_confidentiality(self):
        from review import AuthorityError, AuthorityStore

        candidate = self.create_candidate()
        authority = AuthorityStore(self.root, self.state)

        with self.assertRaises(AuthorityError) as missing_workspace:
            authority.publish_decision(
                "accept",
                candidate["candidate_id"],
                workspace=None,
                confidentiality="personal",
            )
        self.assertEqual(missing_workspace.exception.code, "invalid_workspace")

        with self.assertRaises(AuthorityError) as missing_confidentiality:
            authority.publish_decision(
                "accept",
                candidate["candidate_id"],
                workspace="personal",
                confidentiality=None,
            )
        self.assertEqual(
            missing_confidentiality.exception.code, "invalid_confidentiality"
        )

    def test_workspace_and_confidentiality_ceiling_use_generic_not_found(self):
        from review import AuthorityError, AuthorityStore

        candidate = self.create_candidate(
            title="Restricted work candidate",
            workspace="work",
            confidentiality="restricted",
            content="restricted work candidate content",
        )
        authority = AuthorityStore(self.root, self.state)

        for workspace, confidentiality in (
            ("personal", "restricted"),
            ("work", "internal"),
        ):
            with self.subTest(
                workspace=workspace, confidentiality=confidentiality
            ), self.assertRaises(AuthorityError) as raised:
                authority.publish_decision(
                    "accept",
                    candidate["candidate_id"],
                    workspace=workspace,
                    confidentiality=confidentiality,
                )
            self.assertEqual(raised.exception.code, "candidate_not_found")
            self.assertEqual(str(raised.exception), "candidate not found")

        decision = authority.publish_decision(
            "accept",
            candidate["candidate_id"],
            workspace="work",
            confidentiality="restricted",
        )
        self.assertTrue(decision["decision_id"].startswith("mdec_"))
        self.assertEqual(
            decision["reviewer_scope"],
            {"workspace": "work", "confidentiality": "restricted"},
        )

    def test_decision_digest_binds_reviewer_scope(self):
        from review import AuthorityStore

        candidate = self.create_candidate()
        authority = AuthorityStore(self.root, self.state)

        personal = authority.publish_decision(
            "accept",
            candidate["candidate_id"],
            workspace="personal",
            confidentiality="personal",
        )
        internal = authority.publish_decision(
            "accept",
            candidate["candidate_id"],
            workspace="personal",
            confidentiality="internal",
        )

        self.assertNotEqual(personal["decision_id"], internal["decision_id"])
        personal_record = authority.read_decision(personal["decision_id"])
        internal_record = authority.read_decision(internal["decision_id"])
        self.assertEqual(
            personal_record["reviewer_scope"],
            {"workspace": "personal", "confidentiality": "personal"},
        )
        self.assertEqual(
            internal_record["reviewer_scope"],
            {"workspace": "personal", "confidentiality": "internal"},
        )

    def test_closed_scheme_and_informational_only_refs_fail_closed(self):
        from review.source_refs import SourceRefError, resolve_source_bindings

        base = {
            "workspace": "personal",
            "project": None,
            "confidentiality": "personal",
            "source": "codex",
        }
        for source_refs, expected_code in (
            (["https://example.invalid/evidence"], "invalid_source"),
            (["manual:explanation-only"], "missing_source"),
        ):
            with self.subTest(source_refs=source_refs), self.assertRaises(
                SourceRefError
            ) as raised:
                resolve_source_bindings(
                    self.root,
                    self.state,
                    {**base, "source_refs": source_refs},
                )
            self.assertEqual(raised.exception.code, expected_code)

    def test_manual_declaration_remains_lower_assurance(self):
        from review.source_refs import resolve_source_bindings

        resolved = resolve_source_bindings(
            self.root,
            self.state,
            {
                "workspace": "personal",
                "project": None,
                "confidentiality": "personal",
                "source": "manual:user_confirmed",
                "confirmation": "explicit",
            },
        )

        self.assertEqual(resolved["source_provenance"], "manual_declaration")
        bindings = [json.loads(item) for item in resolved["source_bindings"]]
        self.assertEqual(
            {binding["assurance"] for binding in bindings},
            {"manual_declaration"},
        )

    def test_verified_artifact_binding_is_persisted_and_rechecked(self):
        import memory
        from review import AuthorityError
        from review.gate import ReviewGate
        from review.source_refs import publish_source_artifact

        source_ref = publish_source_artifact(
            self.state,
            kind="review_test_evidence",
            workspace="personal",
            project=None,
            confidentiality="personal",
            payload={"fact": "immutable evidence"},
        )
        candidate = self.create_candidate(
            source="codex",
            confidence="inferred",
            source_refs=[source_ref],
        )
        record, errors = memory.parse_front_matter(self.root / candidate["path"])
        self.assertEqual(errors, [])
        self.assertEqual(record["source_provenance"], "verified")
        bindings = [json.loads(item) for item in record["source_bindings"]]
        verified = [item for item in bindings if item["assurance"] == "verified"]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["canonical_id"], source_ref)
        self.assertEqual(verified[0]["sha256"], source_ref.split(":", 1)[1])

        digest = source_ref.split(":", 1)[1]
        (self.state / "source_artifacts" / f"{digest}.json").unlink()
        with self.assertRaises(AuthorityError) as raised:
            ReviewGate(self.root, self.state).review(
                "accept", candidate["candidate_id"]
            )
        self.assertEqual(raised.exception.code, "provenance_changed")

    def test_verified_source_partition_mismatch_is_rejected(self):
        from review.source_refs import (
            SourceRefError,
            publish_source_artifact,
            resolve_source_bindings,
        )

        source_ref = publish_source_artifact(
            self.state,
            kind="partitioned_review_evidence",
            workspace="personal",
            project=None,
            confidentiality="personal",
            payload={"fact": "personal evidence"},
        )
        with self.assertRaises(SourceRefError) as raised:
            resolve_source_bindings(
                self.root,
                self.state,
                {
                    "workspace": "work",
                    "project": None,
                    "confidentiality": "internal",
                    "source": "codex",
                    "source_refs": [source_ref],
                },
            )
        self.assertEqual(raised.exception.code, "partition_mismatch")


if __name__ == "__main__":
    unittest.main()
