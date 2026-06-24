import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY = REPO_ROOT / "src" / "memory.py"
DISTILL = REPO_ROOT / "src" / "memory_distill.py"


class MemoryDistillReviewTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, *map(str, args)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def init_root(self, tmp):
        root = Path(tmp) / "data"
        self.assertEqual(self.run_cli(MEMORY, "init", "--root", root).returncode, 0)
        return root

    def add_memory(self, root, title="目标记忆", content="旧内容", **kwargs):
        args = [
            MEMORY,
            "add",
            "--root",
            root,
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            title,
            "--scope",
            kwargs.pop("scope", "global"),
            "--workspace",
            kwargs.pop("workspace", "personal"),
            "--confidentiality",
            kwargs.pop("confidentiality", "personal"),
            "--source",
            "user",
            "--confidence",
            "confirmed",
            "--content",
            content,
        ]
        for option, value in kwargs.items():
            if value is not None:
                args.extend(["--" + option.replace("_", "-"), value])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return re.search(r"memory_id: (.+)", result.stdout).group(1)

    def distill_apply(self, root, action="create", **kwargs):
        args = [
            DISTILL,
            "apply",
            "--root",
            root,
            "--action",
            action,
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            kwargs.pop("title", "候选记忆"),
            "--scope",
            kwargs.pop("scope", "global"),
            "--workspace",
            kwargs.pop("workspace", "personal"),
            "--confidentiality",
            kwargs.pop("confidentiality", "personal"),
            "--source",
            kwargs.pop("source", "codex"),
            "--content",
            kwargs.pop("content", "候选内容"),
        ]
        for option, value in kwargs.items():
            flag = "--" + option.replace("_", "-")
            if isinstance(value, list):
                args.append(flag)
                args.extend(value)
            elif value is not None:
                args.extend([flag, value])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return re.search(r"candidate_id: (.+)", result.stdout).group(1)

    def distill_accept(self, root, candidate_id):
        return self.run_cli(DISTILL, "accept", "--root", root, "--id", candidate_id)

    def distill_reject(self, root, candidate_id, reason="not useful"):
        return self.run_cli(DISTILL, "reject", "--root", root, "--id", candidate_id, "--reason", reason)

    def record_by_id(self, root, memory_id):
        for path in (root / "memory").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if re.search(rf'^id: "{re.escape(memory_id)}"$', text, re.MULTILINE):
                return path, text
        self.fail(f"missing memory {memory_id}")

    def test_apply_creates_candidate_not_active_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)

            candidate_id = self.distill_apply(root, evidence=["imports/manual/text/a.md"])
            path, text = self.record_by_id(root, candidate_id)

            self.assertIn('status: "candidate"', text)
            self.assertIn('audit_status: "awaiting_review"', text)
            self.assertIn('candidate_action: "create"', text)
            self.assertIn('confidence: "inferred"', text)
            self.assertTrue(path.name.startswith(candidate_id + "-"))

    def test_accept_create_activates_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root, action="create")

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "active"', text)
            self.assertIn('audit_status: "accepted"', text)
            self.assertIn("reviewed_at:", text)

    def test_accept_merge_only_merges_safe_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, title="目标", content="核心内容")
            candidate_id = self.distill_apply(root, action="merge", target_id=target_id, tags=["new"], source_refs=["doc:one"], relations=["supports:doc"])

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn("核心内容", target)
            self.assertIn("source_refs:", target)
            self.assertIn('  - "doc:one"', target)
            self.assertIn("relations:", target)
            self.assertIn('status: "archived"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)

    def test_accept_support_does_not_change_target_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, content="do not overwrite")
            candidate_id = self.distill_apply(root, action="support", target_id=target_id, content="replacement", evidence=["doc:two"])

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            self.assertIn("do not overwrite", target)
            self.assertNotIn("replacement", target)
            self.assertIn('  - "doc:two"', target)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn('status: "archived"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)

    def test_accept_supersede_links_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root)
            candidate_id = self.distill_apply(root, action="supersede", target_id=target_id)

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn('status: "historical"', target)
            self.assertIn(f'  - "{candidate_id}"', target)
            self.assertIn('status: "active"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)
            self.assertIn(f'  - "{target_id}"', candidate)

    def test_accept_conflict_preserves_target_and_marks_audit_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, content="confirmed content")
            candidate_id = self.distill_apply(root, action="conflict", target_id=target_id, content="conflicting content")

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn("confirmed content", target)
            self.assertNotIn("conflicting content", target)
            self.assertIn('status: "conflict"', candidate)
            self.assertIn('audit_status: "conflict"', candidate)

    def test_reject_records_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root)

            result = self.distill_reject(root, candidate_id, "weak evidence")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "archived"', text)
            self.assertIn('audit_status: "rejected"', text)
            self.assertIn('review_reason: "weak evidence"', text)

    def test_accept_blocks_changed_source_hash_and_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            source = root / "imports/manual/text/source.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("original", encoding="utf-8")
            candidate_id = self.distill_apply(
                root,
                action="merge",
                target_id="missing-target",
                source_path="imports/manual/text/source.md",
                source_sha256="0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5",
            )
            source.write_text("changed", encoding="utf-8")

            result = self.distill_accept(root, candidate_id)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source hash changed", result.stderr)

            source.write_text("original", encoding="utf-8")
            result = self.distill_accept(root, candidate_id)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target memory not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
