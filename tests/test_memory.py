import json
import hashlib
import re
import shutil
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


class ValidateCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_validate(self, root):
        return self.run_cli("validate", "--root", str(root))

    def run_add(self, root, memory_type="principle", title="代码最少原则", scope="global", **kwargs):
        args = [
            "add",
            "--root",
            str(root),
            "--type",
            memory_type,
            "--title",
            title,
            "--scope",
            scope,
            "--workspace",
            kwargs.pop("workspace", "personal"),
            "--confidentiality",
            kwargs.pop("confidentiality", "personal"),
            "--source",
            "user",
            "--confidence",
            "confirmed",
            "--content",
            kwargs.pop("content", "使用尽可能少的代码实现相同功能。"),
        ]
        for option, value in kwargs.items():
            flag = "--" + option.replace("_", "-")
            if isinstance(value, list):
                args.append(flag)
                args.extend(value)
            elif value is not None:
                args.extend([flag, value])
        return self.run_cli(*args)

    def memory_files(self, root):
        return sorted(path for path in (root / "memory").rglob("*.md") if path.is_file())

    def first_memory_file(self, root):
        files = self.memory_files(root)
        self.assertEqual(len(files), 1)
        return files[0]

    def replace_in_file(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def memory_id(self, path):
        match = re.search(r'^id: "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_validate_empty_initialized_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_validate(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Validated: 0 files", result.stdout)
            self.assertIn("Errors: 0", result.stdout)
            self.assertIn("Warnings: 0", result.stdout)

    def test_validate_accepts_valid_principle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, tags=["coding"]).returncode, 0)

            result = self.run_validate(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Validated: 1 files", result.stdout)
            self.assertIn("Errors: 0", result.stdout)

    def test_validate_rejects_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            line = re.search(r'^title: ".*"\n', path.read_text(encoding="utf-8"), re.MULTILINE).group(0)
            self.replace_in_file(path, line, "")

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required field: title", result.stdout)

    def test_validate_rejects_invalid_real_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            self.replace_in_file(path, 'created: "2026-06-22"', 'created: "2026-02-30"')

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("created must be a real YYYY-MM-DD date", result.stdout)

    def test_validate_rejects_unknown_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            self.replace_in_file(path, 'confidence: "confirmed"\n', 'confidence: "confirmed"\nunexpected_field: "x"\n')

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown field: unexpected_field", result.stdout)

    def test_validate_rejects_duplicate_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, title="A").returncode, 0)
            self.assertEqual(self.run_add(root, title="B").returncode, 0)
            first, second = self.memory_files(root)
            duplicate_id = self.memory_id(first)
            second_id = self.memory_id(second)
            self.replace_in_file(second, f'id: "{second_id}"', f'id: "{duplicate_id}"')
            renamed = second.with_name(f"{duplicate_id}-copy.md")
            second.rename(renamed)

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate id", result.stdout)

    def test_validate_rejects_wrong_type_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            source = self.first_memory_file(root)
            target = root / "memory" / "projects" / source.name
            shutil.move(str(source), target)

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("type does not match directory", result.stdout)

    def test_validate_rejects_missing_context_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, "project", "项目", "project", project="demo", context_id="missing").returncode,
                0,
            )

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("context_id does not reference an existing context", result.stdout)

    def test_validate_accepts_existing_context_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, "context", "研究阶段", "context", context_id="ctx-research").returncode,
                0,
            )
            self.assertEqual(
                self.run_add(root, "project", "项目", "project", project="demo", context_id="ctx-research").returncode,
                0,
            )

            result = self.run_validate(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Validated: 2 files", result.stdout)

    def test_validate_rejects_invalid_transition_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(
                    root,
                    "context_transition",
                    "迁移",
                    "context",
                    from_context="missing-a",
                    to_context="missing-b",
                    effective_date="2026-06-22",
                    reason="切换",
                ).returncode,
                0,
            )

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("from_context does not reference an existing context", result.stdout)

    def test_validate_rejects_multiple_active_contexts_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, "context", "一", "context", context_id="ctx-1").returncode, 0)
            self.assertEqual(self.run_add(root, "context", "二", "context", context_id="ctx-2").returncode, 0)

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("multiple active contexts in workspace: personal", result.stdout)

    def test_validate_allows_one_active_context_per_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, "context", "个人", "context", context_id="ctx-p").returncode, 0)
            self.assertEqual(
                self.run_add(
                    root,
                    "context",
                    "工作",
                    "context",
                    context_id="ctx-w",
                    workspace="work",
                    confidentiality="internal",
                ).returncode,
                0,
            )

            result = self.run_validate(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validate_rejects_invalid_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, valid_from="2026-06-22", valid_until="2026-06-21").returncode,
                0,
            )

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("valid_until cannot be earlier than valid_from", result.stdout)

    def test_validate_rejects_self_supersedes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            memory_id = self.memory_id(path)
            self.replace_in_file(path, "tags: []\n", f"supersedes:\n  - \"{memory_id}\"\ntags: []\n")

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("supersedes cannot reference itself", result.stdout)

    def test_validate_rejects_unclosed_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("---\n\n# 代码最少原则", "\n\n# 代码最少原则", 1), encoding="utf-8")

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unclosed front matter", result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_validate_rejects_filename_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.first_memory_file(root)
            renamed = path.with_name("wrong-id.md")
            path.rename(renamed)

            result = self.run_validate(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filename must start with id-", result.stdout)

    def test_validate_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.memory_files(root)
            }

            result = self.run_validate(root)

            after = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.memory_files(root)
            }
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
