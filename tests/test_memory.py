import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
CSV_HEADER = "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags"

EXPECTED_DIRS = [
    "memory/profile",
    "memory/contexts",
    "memory/transitions",
    "memory/principles",
    "memory/projects",
    "memory/decisions",
    "memory/procedures",
    "memory/sessions",
    "literature/inbox",
    "literature/pdf",
    "literature/notes",
    "literature/journals",
    "manuscripts/current",
    "manuscripts/evidence",
    "manuscripts/archive",
    "exports/database_snapshots",
    "backups",
]


class InitCommandTests(unittest.TestCase):
    def run_init(self, root):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def test_init_creates_expected_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            result = self.run_init(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in EXPECTED_DIRS:
                self.assertTrue((root / relative).is_dir(), relative)

            marker = root / ".research-agent-root"
            manifest = root / "exports" / "index_manifest.json"
            matrix = root / "literature" / "literature_matrix.csv"
            self.assertTrue(marker.is_file())
            self.assertTrue(manifest.is_file())
            self.assertTrue(matrix.is_file())
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8")),
                {"format_version": 1, "type": "research-agent-data-root"},
            )
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8")),
                {"format_version": 1, "records": []},
            )
            self.assertEqual(matrix.read_text(encoding="utf-8").strip(), CSV_HEADER)

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"

            first = self.run_init(root)
            second = self.run_init(root)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)

    def test_init_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            manifest = root / "exports" / "index_manifest.json"
            custom = '{"format_version": 99, "records": ["keep"]}'

            self.assertEqual(self.run_init(root).returncode, 0)
            manifest.write_text(custom, encoding="utf-8")
            self.assertEqual(self.run_init(root).returncode, 0)

            self.assertEqual(manifest.read_text(encoding="utf-8"), custom)

    def test_init_rejects_repository_root(self):
        result = self.run_init(REPO_ROOT)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("数据目录不能等于代码仓库目录。", result.stderr)


class AddCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_add(self, root, *extra):
        base = [
            "add",
            "--root",
            str(root),
            "--type",
            "principle",
            "--title",
            "代码最少原则",
            "--scope",
            "global",
            "--workspace",
            "personal",
            "--confidentiality",
            "personal",
            "--source",
            "user",
            "--confidence",
            "confirmed",
            "--content",
            "使用尽可能少的代码实现相同功能。",
        ]
        return self.run_cli(*(base + list(extra)))

    def memory_files(self, root):
        memory = root / "memory"
        if not memory.exists():
            return []
        return sorted(path for path in memory.rglob("*.md") if path.is_file())

    def test_add_principle_creates_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_add(root, "--tags", "coding", "architecture")

            self.assertEqual(result.returncode, 0, result.stderr)
            files = self.memory_files(root)
            self.assertEqual(len(files), 1)
            created = files[0]
            self.assertEqual(created.parent, root / "memory" / "principles")
            self.assertRegex(
                created.name,
                r"^principle-\d{8}-[0-9a-f]{8}-代码最少原则\.md$",
            )
            text = created.read_text(encoding="utf-8")
            self.assertRegex(text, r'id: "principle-\d{8}-[0-9a-f]{8}"')
            self.assertIn('type: "principle"', text)
            self.assertIn('title: "代码最少原则"', text)
            self.assertIn('scope: "global"', text)
            self.assertIn("content: |-\n  使用尽可能少的代码实现相同功能。", text)
            self.assertIn("tags:\n  - \"coding\"\n  - \"architecture\"", text)
            self.assertIn("# 代码最少原则", text)
            self.assertIn("该记忆的结构化内容保存在 front matter 的 content 字段中。", text)
            self.assertIn(str(created.relative_to(root)), result.stdout)

    def test_add_same_title_twice_creates_distinct_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            first = self.run_add(root)
            second = self.run_add(root)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            files = self.memory_files(root)
            self.assertEqual(len(files), 2)
            self.assertNotEqual(files[0].name, files[1].name)

    def test_add_rejects_uninitialized_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            root.mkdir()

            result = self.run_add(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("python3 src/memory.py init --root PATH", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_context_requires_context_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_cli(
                "add",
                "--root",
                str(root),
                "--type",
                "context",
                "--title",
                "研究阶段",
                "--scope",
                "context",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "当前情景。",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--context-id", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_context_transition_requires_transition_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_cli(
                "add",
                "--root",
                str(root),
                "--type",
                "context_transition",
                "--title",
                "情景迁移",
                "--scope",
                "context",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "迁移记录。",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--from-context", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_project_scope_requires_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_cli(
                "add",
                "--root",
                str(root),
                "--type",
                "decision",
                "--title",
                "项目决定",
                "--scope",
                "project",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "决定内容。",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--project", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_rejects_restricted_personal_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_cli(
                "add",
                "--root",
                str(root),
                "--type",
                "principle",
                "--title",
                "受限原则",
                "--scope",
                "global",
                "--workspace",
                "personal",
                "--confidentiality",
                "restricted",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "受限内容。",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("workspace", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_rejects_invalid_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_add(root, "--valid-from", "2026-99-99")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("YYYY-MM-DD", result.stderr)
            self.assertEqual(self.memory_files(root), [])

    def test_add_title_cannot_escape_target_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_cli(
                "add",
                "--root",
                str(root),
                "--type",
                "principle",
                "--title",
                "../../坏/标题\\escape",
                "--scope",
                "global",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "路径逃逸测试。",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            files = self.memory_files(root)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].parent, root / "memory" / "principles")
            self.assertNotIn("..", files[0].name)
            self.assertTrue(files[0].resolve().is_relative_to((root / "memory" / "principles").resolve()))


if __name__ == "__main__":
    unittest.main()
