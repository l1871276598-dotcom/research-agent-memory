import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


def canonical_json(value):
    # LAOS Canonical JSON v1 (evidence-hash-semantics-v1). ensure_ascii=False
    # matches the Bridge/Node canonicalizer in the text domain.
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha(payload):
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def evidence_input(**overrides):
    values = {
        "schema_version": 2,
        "kind": "vault_note_snapshot",
        "source": {
            "scheme": "vault-note",
            "note_id": "01HXYZ123",
            "source_sha256": None,
        },
        "locator": {"relative_path": "Projects/LAOS/design.md"},
        "payload": {"content": "note body", "metadata": {"title": "design"}},
        "payload_sha256": None,
        "workspace": "personal",
        "project": "laos",
        "confidentiality": "personal",
    }
    values.update(overrides)
    content = values["payload"].get("content") if isinstance(values["payload"], dict) else None
    if values["payload_sha256"] is None:
        values["payload_sha256"] = payload_sha(values["payload"])
    if values["source"]["source_sha256"] is None and isinstance(content, str):
        values["source"]["source_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return values


class EvidenceAgentTests(unittest.TestCase):
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
        return self.agent().run(task, {})

    def test_publishes_artifact_and_returns_canonical_identity(self):
        task = {"type": "evidence.publish", "input": evidence_input()}
        result = self.publish(task)

        output = result["output"]
        self.assertTrue(output["source_ref"].startswith("artifact:"))
        self.assertEqual(len(output["artifact_sha256"]), 64)
        # canonical identity derives from source_sha256, NOT artifact_sha256
        # (F-03: three digests must not be conflated).
        content = "note body"
        expected_source = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(output["source_sha256"], expected_source)
        self.assertEqual(
            output["canonical_identity"],
            f"vault-note:01HXYZ123@{output['source_sha256']}",
        )
        self.assertEqual(output["payload_sha256"], payload_sha({"content": content, "metadata": {"title": "design"}}))
        self.assertNotEqual(output["artifact_sha256"], output["source_sha256"])
        self.assertNotEqual(output["artifact_sha256"], output["payload_sha256"])
        self.assertEqual(output["workspace"], "personal")
        self.assertEqual(output["project"], "laos")
        self.assertEqual(output["confidentiality"], "personal")
        # Ingress does not create candidates, decisions, or activations.
        self.assertEqual(result["candidates"], [])

    def test_repeated_publish_is_idempotent(self):
        task = {"type": "evidence.publish", "input": evidence_input()}
        first = self.publish(task)["output"]["source_ref"]
        second = self.publish(task)["output"]["source_ref"]
        self.assertEqual(first, second)

    def test_canonicalization_is_key_order_independent(self):
        base = {"content": "c", "metadata": {"title": "design"}}
        task_a = {"type": "evidence.publish", "input": evidence_input(payload=base)}
        reordered = {"metadata": {"title": "design"}, "content": "c"}
        task_b = {"type": "evidence.publish", "input": evidence_input(payload=reordered)}
        self.assertEqual(
            self.publish(task_a)["output"]["source_ref"],
            self.publish(task_b)["output"]["source_ref"],
        )

    def test_core_recomputes_payload_sha_instead_of_trusting_caller(self):
        # A caller who supplies a WRONG payload_sha256 for the actual payload
        # must still yield a stored artifact whose payload_sha256 matches what
        # Core computed from the bytes it saw. The stored artifact is the truth.
        task = {
            "type": "evidence.publish",
            "input": evidence_input(payload_sha256="f" * 64),
        }
        output = self.publish(task)["output"]
        actual_payload = {"content": "note body", "metadata": {"title": "design"}}
        self.assertEqual(output["payload_sha256"], payload_sha(actual_payload))
        self.assertNotEqual(output["payload_sha256"], "f" * 64)

    def test_core_recomputes_source_sha_instead_of_trusting_caller(self):
        # A caller-supplied source_sha256 is ignored; Core derives it from the
        # payload content bytes. F-02 defense.
        fake = "a" * 64
        task = {
            "type": "evidence.publish",
            "input": evidence_input(source={"scheme": "vault-note", "note_id": "01HXYZ123", "source_sha256": fake}),
        }
        output = self.publish(task)["output"]
        expected = hashlib.sha256("note body".encode("utf-8")).hexdigest()
        self.assertEqual(output["source_sha256"], expected)
        self.assertNotEqual(output["source_sha256"], fake)

    def test_same_payload_different_note_id_produce_different_artifacts(self):
        # F-02: same payload + different source identity MUST be different
        # artifacts.
        base = {"content": "c", "metadata": {"title": "t"}}
        task_a = {"type": "evidence.publish", "input": evidence_input(payload=base, source={"scheme": "vault-note", "note_id": "AAA", "source_sha256": None})}
        task_b = {"type": "evidence.publish", "input": evidence_input(payload=base, source={"scheme": "vault-note", "note_id": "BBB", "source_sha256": None})}
        self.assertNotEqual(
            self.publish(task_a)["output"]["source_ref"],
            self.publish(task_b)["output"]["source_ref"],
        )

    def test_same_note_id_different_content_produce_different_artifacts(self):
        # Content change -> source_sha256/canonical identity/artifact all change.
        task_a = {"type": "evidence.publish", "input": evidence_input(payload={"content": "v1", "metadata": {"title": "t"}})}
        task_b = {"type": "evidence.publish", "input": evidence_input(payload={"content": "v2", "metadata": {"title": "t"}})}
        a = self.publish(task_a)["output"]
        b = self.publish(task_b)["output"]
        self.assertNotEqual(a["source_sha256"], b["source_sha256"])
        self.assertNotEqual(a["canonical_identity"], b["canonical_identity"])
        self.assertNotEqual(a["artifact_sha256"], b["artifact_sha256"])

    def test_rename_only_changes_artifact_not_identity(self):
        # Rename (locator change) keeps canonical identity + source_sha256 but
        # can produce a new artifact.
        base = {"content": "same", "metadata": {"title": "t"}}
        task_a = {"type": "evidence.publish", "input": evidence_input(payload=base, locator={"relative_path": "A/design.md"})}
        task_b = {"type": "evidence.publish", "input": evidence_input(payload=base, locator={"relative_path": "B/design.md"})}
        a = self.publish(task_a)["output"]
        b = self.publish(task_b)["output"]
        self.assertEqual(a["source_sha256"], b["source_sha256"])
        self.assertEqual(a["canonical_identity"], b["canonical_identity"])
        self.assertNotEqual(a["artifact_sha256"], b["artifact_sha256"])

    def test_unsupported_schema_version_rejected(self):
        task = {"type": "evidence.publish", "input": evidence_input(schema_version=1)}
        with self.assertRaises(ValueError):
            self.publish(task)

    def test_rejects_invalid_source(self):
        for source in [
            {"scheme": "file", "note_id": "x", "source_sha256": "0" * 64},
            {"scheme": "vault-note", "note_id": "../escape", "source_sha256": "0" * 64},
            {"scheme": "vault-note", "note_id": "", "source_sha256": "0" * 64},
            {"scheme": "vault-note", "note_id": "ok", "source_sha256": "short"},
        ]:
            with self.subTest(source=source):
                task = {"type": "evidence.publish", "input": evidence_input(source=source)}
                with self.assertRaises(ValueError):
                    self.publish(task)

    def test_rejects_invalid_payload_sha(self):
        task = {
            "type": "evidence.publish",
            "input": evidence_input(payload_sha256="z" * 64),
        }
        with self.assertRaises(ValueError):
            self.publish(task)

    def test_rejects_payload_without_content(self):
        task = {
            "type": "evidence.publish",
            "input": evidence_input(payload={"metadata": {"title": "t"}}),
        }
        with self.assertRaises(ValueError):
            self.publish(task)

    def test_rejects_bad_workspace(self):
        task = {"type": "evidence.publish", "input": evidence_input(workspace="evil")}
        with self.assertRaises(ValueError):
            self.publish(task)

    def test_rejects_bad_confidentiality(self):
        task = {
            "type": "evidence.publish",
            "input": evidence_input(confidentiality="topsecret"),
        }
        with self.assertRaises(ValueError):
            self.publish(task)

    def test_v2_artifact_re_resolves_as_verified_binding(self):
        from review.source_refs import _resolve_state_artifact

        out = self.publish({"type": "evidence.publish", "input": evidence_input()})["output"]
        resolved = _resolve_state_artifact(self.state, out["artifact_sha256"])
        self.assertEqual(resolved["assurance"], "verified")
        self.assertEqual(resolved["sha256"], out["artifact_sha256"])
        self.assertEqual(resolved["kind"], "vault_note_snapshot")
        self.assertEqual(
            resolved["canonical_id"],
            f"artifact:{out['artifact_sha256']}",
        )

    def test_publish_does_not_change_memory_authority(self):
        from memory.store import MemoryStore
        from review import AuthorityMemoryStore, AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        store = AuthorityMemoryStore(MemoryStore(self.root), authority)
        before_generation = authority.current_generation()

        self.publish({"type": "evidence.publish", "input": evidence_input()})

        self.assertEqual(authority.current_generation(), before_generation)
        self.assertEqual(store.active_relevant("anything", "personal"), [])


class EvidenceCliTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def run_cli(self, task):
        env = {"PYTHONPATH": str(SOURCE_DIR), "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(
            [
                sys.executable,
                str(SOURCE_DIR / "laos.py"),
                "--root", str(self.root),
                "--state-dir", str(self.state),
                "--task-json", json.dumps(task),
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(SOURCE_DIR),
        )

    def test_cli_dispatch_evidence_publish(self):
        task = {"type": "evidence.publish", "input": evidence_input()}
        result = self.run_cli(task)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["agent_id"], "evidence_agent")
        self.assertTrue(parsed["output"]["source_ref"].startswith("artifact:"))
        self.assertEqual(parsed["candidates"], [])

    def test_cli_rejects_unknown_task_type(self):
        result = self.run_cli({"type": "not.a.task", "input": {}})
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
