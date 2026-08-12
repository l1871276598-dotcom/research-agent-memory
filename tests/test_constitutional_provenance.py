"""Constitutional invariants (C-INV-14/15/16/17) enforced at the Core boundary.

These tests pin the Core-side half of the frozen evidence contract: Core
recomputes every digest itself, never trusts a caller-supplied digest, and the
artifact body binds source identity so same-payload-different-note_id yields
different artifacts (F-02). The Bridge-side half lives in the Developer Bridge
repo (test/constitutional-*.test.js).
"""

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import sys
from pathlib import Path as _P

_REPO = _P(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def source_sha(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ConstitutionalProvenanceTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def agent(self):
        from agents.evidence import EvidenceAgent

        return EvidenceAgent(str(self.state))

    def publish(self, task):
        return self.agent().run(task, {})["output"]

    def input(self, **overrides):
        values = {
            "schema_version": 2,
            "kind": "vault_note_snapshot",
            "source": {"scheme": "vault-note", "note_id": "n001", "source_sha256": None},
            "locator": {"relative_path": "P/n.md"},
            "payload": {"content": "body", "metadata": {"title": "t"}},
            "payload_sha256": None,
            "workspace": "personal",
            "project": "laos",
            "confidentiality": "personal",
        }
        values.update(overrides)
        if values["source"]["source_sha256"] is None:
            values["source"]["source_sha256"] = source_sha(values["payload"]["content"])
        if values["payload_sha256"] is None:
            values["payload_sha256"] = payload_sha(values["payload"])
        return values

    # C-INV-15: three digests are distinct and independently frozen.
    def test_three_digests_are_distinct_and_consistent(self):
        out = self.publish({"type": "evidence.publish", "input": self.input()})
        self.assertNotEqual(out["source_sha256"], out["payload_sha256"])
        self.assertNotEqual(out["source_sha256"], out["artifact_sha256"])
        self.assertNotEqual(out["payload_sha256"], out["artifact_sha256"])
        self.assertEqual(out["source_sha256"], source_sha("body"))
        self.assertEqual(out["payload_sha256"], payload_sha({"content": "body", "metadata": {"title": "t"}}))

    # C-INV-14: artifact binds source identity.
    def test_same_payload_different_note_id_different_artifact(self):
        a = self.publish({"type": "evidence.publish", "input": self.input(source={"scheme": "vault-note", "note_id": "AAA", "source_sha256": None})})
        b = self.publish({"type": "evidence.publish", "input": self.input(source={"scheme": "vault-note", "note_id": "BBB", "source_sha256": None})})
        self.assertNotEqual(a["artifact_sha256"], b["artifact_sha256"])
        self.assertNotEqual(a["canonical_identity"], b["canonical_identity"])

    # C-INV-14: artifact binds the locator (rename changes artifact, not identity).
    def test_rename_changes_artifact_not_identity(self):
        a = self.publish({"type": "evidence.publish", "input": self.input(locator={"relative_path": "A/n.md"})})
        b = self.publish({"type": "evidence.publish", "input": self.input(locator={"relative_path": "B/n.md"})})
        self.assertEqual(a["source_sha256"], b["source_sha256"])
        self.assertEqual(a["canonical_identity"], b["canonical_identity"])
        self.assertNotEqual(a["artifact_sha256"], b["artifact_sha256"])

    # C-INV-15: Core recomputes payload_sha256, never trusts the caller.
    def test_core_recomputes_payload_sha(self):
        out = self.publish({"type": "evidence.publish", "input": self.input(payload_sha256="f" * 64)})
        self.assertEqual(out["payload_sha256"], payload_sha({"content": "body", "metadata": {"title": "t"}}))
        self.assertNotEqual(out["payload_sha256"], "f" * 64)

    # C-INV-15: Core recomputes source_sha256 from the content bytes it sees.
    def test_core_recomputes_source_sha(self):
        out = self.publish({"type": "evidence.publish", "input": self.input(source={"scheme": "vault-note", "note_id": "n001", "source_sha256": "a" * 64})})
        self.assertEqual(out["source_sha256"], source_sha("body"))
        self.assertNotEqual(out["source_sha256"], "a" * 64)

    # C-INV-16: evidence ingress never mutates memory authority.
    def test_publish_never_mutates_authority(self):
        from memory.store import MemoryStore
        from review import AuthorityMemoryStore, AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        store = AuthorityMemoryStore(MemoryStore(self.root), authority)
        before = authority.current_generation()
        self.publish({"type": "evidence.publish", "input": self.input()})
        self.assertEqual(authority.current_generation(), before)
        self.assertEqual(store.active_relevant("x", "personal"), [])

    # C-INV-14: artifact re-resolves as a verified binding.
    def test_artifact_resolves_verified(self):
        from review.source_refs import _resolve_state_artifact

        out = self.publish({"type": "evidence.publish", "input": self.input()})
        resolved = _resolve_state_artifact(self.state, out["artifact_sha256"])
        self.assertEqual(resolved["assurance"], "verified")
        self.assertEqual(resolved["sha256"], out["artifact_sha256"])

    # C-INV-17: identity is cryptographically bound to content (content change
    # changes source_sha/canonical identity/artifact together).
    def test_content_change_changes_all_bindings(self):
        a = self.publish({"type": "evidence.publish", "input": self.input(payload={"content": "v1", "metadata": {"title": "t"}})})
        b = self.publish({"type": "evidence.publish", "input": self.input(payload={"content": "v2", "metadata": {"title": "t"}})})
        self.assertNotEqual(a["source_sha256"], b["source_sha256"])
        self.assertNotEqual(a["canonical_identity"], b["canonical_identity"])
        self.assertNotEqual(a["artifact_sha256"], b["artifact_sha256"])


if __name__ == "__main__":
    unittest.main()
