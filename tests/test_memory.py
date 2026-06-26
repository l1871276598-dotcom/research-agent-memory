import json
import hashlib
import importlib.util
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
CSV_HEADER = "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags"


def load_memory_module():
    spec = importlib.util.spec_from_file_location("memory_module_for_tests", MEMORY_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

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
            text = path.read_text(encoding="utf-8")
            created_line = re.search(r'^created: "\d{4}-\d{2}-\d{2}"$', text, re.MULTILINE).group(0)
            self.replace_in_file(path, created_line, 'created: "2026-02-30"')

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


class ContextTransitionCommandTests(unittest.TestCase):
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

    def run_transition(self, root, *extra):
        base = [
            "context-transition",
            "--root",
            str(root),
            "--from-context",
            "university-student",
            "--to-context",
            "industry-engineer",
            "--to-title",
            "企业研发阶段",
            "--workspace",
            "work",
            "--confidentiality",
            "internal",
            "--effective-date",
            "2027-07-01",
            "--reason",
            "毕业后进入企业研发岗位",
        ]
        return self.run_cli(*(base + list(extra)))

    def run_add_context(self, root, context_id, title="研究生阶段", workspace="personal", confidentiality="personal", status="active", valid_from="2023-09-01", content="当前处于研究生科研阶段。"):
        args = [
            "add",
            "--root",
            str(root),
            "--type",
            "context",
            "--title",
            title,
            "--scope",
            "context",
            "--workspace",
            workspace,
            "--confidentiality",
            confidentiality,
            "--source",
            "user",
            "--confidence",
            "confirmed",
            "--content",
            content,
            "--context-id",
            context_id,
            "--valid-from",
            valid_from,
            "--tags",
            "education",
            "research",
            "--status",
            status,
        ]
        return self.run_cli(*args)

    def records(self, root):
        module = load_memory_module()
        items = []
        for path in sorted((root / "memory").rglob("*.md")):
            record, errors = module.parse_front_matter(path)
            self.assertEqual(errors, [], path)
            items.append((path, record))
        return items

    def record_by_context(self, root, context_id):
        matches = [(path, record) for path, record in self.records(root) if record.get("context_id") == context_id]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def transition_records(self, root):
        return [
            (path, record)
            for path, record in self.records(root)
            if record.get("type") == "context_transition"
        ]

    def hashes(self, root):
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "memory").rglob("*.md"))
        }

    def all_names(self, root):
        return sorted(path.name for path in (root / "memory").rglob("*") if path.is_file())

    def test_context_transition_same_workspace_updates_source_and_creates_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            result = self.run_transition(root, "--workspace", "personal", "--confidentiality", "personal")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source_path, source = self.record_by_context(root, "university-student")
            target_path, target = self.record_by_context(root, "industry-engineer")
            transitions = self.transition_records(root)
            self.assertEqual(source["status"], "historical")
            self.assertEqual(source["valid_until"], "2027-06-30")
            self.assertEqual(target["status"], "active")
            self.assertEqual(target_path.parent, root / "memory" / "contexts")
            self.assertEqual(len(transitions), 1)
            self.assertIn(source["id"], target["supersedes"])
            self.assertIn(target["id"], source["superseded_by"])
            self.assertIn(f"Updated: {source_path.relative_to(root)}", result.stdout)

    def test_context_transition_cross_workspace_creates_work_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            result = self.run_transition(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_context(root, "industry-engineer")
            self.assertEqual(target["workspace"], "work")
            self.assertEqual(target["confidentiality"], "internal")
            self.assertEqual(self.run_validate(root).returncode, 0)

    def test_context_transition_preserves_source_body_after_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            source_path, _ = self.record_by_context(root, "university-student")
            before_body = source_path.read_text(encoding="utf-8").split("---", 2)[2]

            self.assertEqual(self.run_transition(root).returncode, 0)

            after_body = source_path.read_text(encoding="utf-8").split("---", 2)[2]
            self.assertEqual(after_body, before_body)

    def test_context_transition_sets_supersession_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            self.assertEqual(self.run_transition(root).returncode, 0)

            _, source = self.record_by_context(root, "university-student")
            _, target = self.record_by_context(root, "industry-engineer")
            self.assertEqual(source["superseded_by"], [target["id"]])
            self.assertEqual(target["supersedes"], [source["id"]])

    def test_context_transition_record_contains_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            self.assertEqual(self.run_transition(root, "--tags", "career", "work").returncode, 0)

            [(path, record)] = self.transition_records(root)
            self.assertEqual(path.parent, root / "memory" / "transitions")
            self.assertEqual(record["type"], "context_transition")
            self.assertEqual(record["scope"], "context")
            self.assertEqual(record["from_context"], "university-student")
            self.assertEqual(record["to_context"], "industry-engineer")
            self.assertEqual(record["effective_date"], "2027-07-01")
            self.assertEqual(record["reason"], "毕业后进入企业研发岗位")
            self.assertEqual(record["content"], "毕业后进入企业研发岗位")
            self.assertEqual(record["tags"], ["career", "work"])
            self.assertNotIn("context_id", record)

    def test_context_transition_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            before = self.hashes(root)

            result = self.run_transition(root, "--dry-run")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.hashes(root), before)
            self.assertIn("Dry run: no files changed", result.stdout)
            self.assertIn("New context: memory/contexts/", result.stdout)
            self.assertIn("Transition: memory/transitions/", result.stdout)

    def test_context_transition_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_transition(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Transition aborted because validation failed.", result.stderr)
            self.assertEqual(self.records(root), [])

    def test_context_transition_rejects_inactive_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student", status="historical").returncode, 0)
            before = self.hashes(root)

            result = self.run_transition(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source context must be active", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_rejects_existing_target_context_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            self.assertEqual(
                self.run_add_context(root, "industry-engineer", workspace="work", confidentiality="internal").returncode,
                0,
            )
            before = self.hashes(root)

            result = self.run_transition(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target context_id already exists", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_rejects_same_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            before = self.hashes(root)

            result = self.run_transition(root, "--to-context", "university-student")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("to-context must differ from from-context", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_rejects_active_target_workspace_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            self.assertEqual(
                self.run_add_context(root, "work-now", title="工作阶段", workspace="work", confidentiality="internal").returncode,
                0,
            )
            before = self.hashes(root)

            result = self.run_transition(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target workspace already has an active context", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_allows_source_as_only_same_workspace_active_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            result = self.run_transition(root, "--workspace", "personal", "--confidentiality", "personal")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.run_validate(root).returncode, 0)

    def test_context_transition_rejects_invalid_effective_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            before = self.hashes(root)

            result = self.run_transition(root, "--effective-date", "2027-02-30")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("effective-date", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_rejects_effective_date_not_after_valid_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            before = self.hashes(root)

            result = self.run_transition(root, "--effective-date", "2023-09-01")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("effective-date must be later than source valid_from", result.stderr)
            self.assertEqual(self.hashes(root), before)

    def test_context_transition_rejects_invalid_store_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            source_path, _ = self.record_by_context(root, "university-student")
            before = self.hashes(root)
            source_path.write_text(source_path.read_text(encoding="utf-8").replace('title: "研究生阶段"\n', "", 1), encoding="utf-8")
            invalid = self.hashes(root)

            result = self.run_transition(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Transition aborted because validation failed.", result.stderr)
            self.assertEqual(self.hashes(root), invalid)
            self.assertNotEqual(before, invalid)

    def test_context_transition_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            self.assertEqual(self.run_transition(root).returncode, 0)

            self.assertFalse([name for name in self.all_names(root) if name.startswith(".tmp-")])

    def test_context_transition_rolls_back_when_create_fails_before_source_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            module = load_memory_module()
            self.assertEqual(module.init_store(root)[0], root.resolve())
            add_args = mock.Mock(
                root=str(root),
                type="context",
                title="研究生阶段",
                scope="context",
                workspace="personal",
                confidentiality="personal",
                source="user",
                confidence="confirmed",
                content="当前处于研究生科研阶段。",
                status="active",
                context_id="university-student",
                project=None,
                valid_from="2023-09-01",
                valid_until=None,
                tags=["education", "research"],
                from_context=None,
                to_context=None,
                effective_date=None,
                reason=None,
            )
            module.add_memory(add_args)
            before = self.hashes(root)
            transition_args = mock.Mock(
                root=str(root),
                from_context="university-student",
                to_context="industry-engineer",
                to_title="企业研发阶段",
                workspace="work",
                confidentiality="internal",
                effective_date="2027-07-01",
                reason="毕业后进入企业研发岗位",
                source="user",
                confidence="confirmed",
                content=None,
                tags=None,
                dry_run=False,
            )

            original_replace = Path.replace
            calls = []

            def fail_second_replace(path, target):
                calls.append(Path(target).name)
                if len(calls) == 2:
                    raise OSError("disk full")
                return original_replace(path, target)

            with mock.patch.object(Path, "replace", fail_second_replace):
                with self.assertRaises(OSError):
                    module.context_transition(transition_args)

            self.assertEqual(self.hashes(root), before)
            self.assertFalse([name for name in self.all_names(root) if name.startswith(".tmp-")])


class DbInitCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_db_init(self, root, state_dir):
        return self.run_cli("db-init", "--root", str(root), "--state-dir", str(state_dir))

    def run_add(self, root):
        return self.run_cli(
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
        )

    def db_path(self, state_dir):
        return state_dir / "memory.sqlite"

    def sqlite_objects(self, db):
        with sqlite3.connect(db) as conn:
            return dict(
                conn.execute(
                    """
                    SELECT name, type
                    FROM sqlite_master
                    WHERE name IN ('memories', 'memory_fts', 'index_state')
                    """
                ).fetchall()
            )

    def columns(self, db, table):
        with sqlite3.connect(db) as conn:
            return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]

    def test_db_init_creates_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(self.db_path(state_dir).is_file())
            self.assertIn("Database initialized", result.stdout)

    def test_db_init_creates_expected_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
            db = self.db_path(state_dir)

            with sqlite3.connect(db) as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                objects = self.sqlite_objects(db)
                conn.execute(
                    "INSERT INTO memory_fts(id, title, content, tags) VALUES (?, ?, ?, ?)",
                    ("probe", "代码最少原则", "使用更少代码实现功能", '["coding"]'),
                )
                result = conn.execute(
                    "SELECT id FROM memory_fts WHERE memory_fts MATCH ?",
                    ("代码*",),
                ).fetchall()

            self.assertEqual(version, 2)
            self.assertEqual(mode.lower(), "wal")
            self.assertEqual(objects, {"memories": "table", "memory_fts": "table", "index_state": "table"})
            self.assertEqual(
                self.columns(db, "memories"),
                [
                    "id",
                    "schema",
                    "tier",
                    "type",
                    "title",
                    "content",
                    "body",
                    "tags",
                    "aliases",
                    "status",
                    "scope",
                    "workspace",
                    "confidentiality",
                    "source",
                    "confidence",
                    "source_id",
                    "source_path",
                    "source_sha256",
                    "context_id",
                    "project",
                    "valid_from",
                    "valid_until",
                    "created",
                    "updated",
                    "relative_path",
                    "sha256",
                    "metadata_json",
                ],
            )
            self.assertEqual(
                self.columns(db, "index_state"),
                ["relative_path", "sha256", "mtime_ns", "indexed_at"],
            )
            self.assertEqual(result, [("probe",)])

    def test_db_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            first = self.run_db_init(root, state_dir)
            second = self.run_db_init(root, state_dir)

            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("Database already initialized", second.stdout)
            self.assertEqual(self.sqlite_objects(self.db_path(state_dir))["memory_fts"], "table")

    def test_db_init_creates_only_database_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)

            self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)

            self.assertEqual(sorted(path.name for path in state_dir.iterdir()), ["memory.sqlite"])

    def test_db_init_rejects_uninitialized_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            state_dir = Path(tmp) / "state"

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(state_dir.exists())

    def test_db_init_rejects_invalid_memory_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            memory_file = next((root / "memory").rglob("*.md"))
            memory_file.write_text(
                memory_file.read_text(encoding="utf-8").replace('title: "代码最少原则"\n', "", 1),
                encoding="utf-8",
            )

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Database initialization aborted because validation failed.", result.stderr)
            self.assertFalse(state_dir.exists())

    def test_db_init_rejects_state_dir_inside_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, root / "state")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SQLite state directory must be local", result.stderr)

    def test_db_init_rejects_state_dir_equal_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SQLite state directory must be local", result.stderr)

    def test_db_init_rejects_data_root_inside_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            root = state_dir / "data"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.db_path(state_dir).exists())

    def test_db_init_rejects_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, REPO_ROOT)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SQLite state directory must be local", result.stderr)

    def test_db_init_rejects_repository_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, REPO_ROOT / "state")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SQLite state directory must be local", result.stderr)

    def test_db_init_rejects_icloud_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_memory_module()
            root = Path(tmp) / "data"
            fake_icloud = Path(tmp) / "icloud"
            state_dir = fake_icloud / "state"
            module.init_store(root)
            args = mock.Mock(root=str(root), state_dir=str(state_dir))

            with mock.patch.object(module, "_icloud_root", return_value=fake_icloud.resolve()):
                with self.assertRaisesRegex(ValueError, "SQLite state directory must be local"):
                    module.db_init(args)
            self.assertFalse(state_dir.exists())

    def test_db_init_rejects_state_dir_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = Path(tmp) / "target"
            state_dir = Path(tmp) / "state-link"
            target.mkdir()
            state_dir.symlink_to(target, target_is_directory=True)
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.db_path(target).exists())

    def test_db_init_rejects_database_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            target = Path(tmp) / "target.sqlite"
            state_dir.mkdir()
            target.write_text("", encoding="utf-8")
            self.db_path(state_dir).symlink_to(target)
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "")

    def test_db_init_rejects_incompatible_user_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            db = self.db_path(state_dir)
            with sqlite3.connect(db) as conn:
                conn.execute("PRAGMA user_version = 2")
            before = db.read_bytes()
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(db.read_bytes(), before)

    def test_db_init_rejects_corrupt_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            state_dir.mkdir()
            db = self.db_path(state_dir)
            db.write_text("not sqlite", encoding="utf-8")
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_db_init(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(db.read_text(encoding="utf-8"), "not sqlite")

    def test_db_init_preserves_existing_valid_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
            db = self.db_path(state_dir)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    """
                    INSERT INTO memories (
                        id, type, title, content, tags, status, scope, workspace,
                        confidentiality, source, confidence, created, updated,
                        relative_path, sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "m1",
                        "principle",
                        "原则",
                        "内容",
                        "[]",
                        "active",
                        "global",
                        "personal",
                        "personal",
                        "user",
                        "confirmed",
                        "2026-06-22",
                        "2026-06-22",
                        "memory/principles/m1.md",
                        "abc",
                    ),
                )

            result = self.run_db_init(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with sqlite3.connect(db) as conn:
                rows = conn.execute("SELECT id FROM memories").fetchall()
            self.assertEqual(rows, [("m1",)])

    def test_db_init_handles_missing_fts5(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_memory_module()
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            module.init_store(root)
            args = mock.Mock(root=str(root), state_dir=str(state_dir))

            with mock.patch.object(module, "check_fts5", side_effect=ValueError("SQLite FTS5 is not available in this Python build.")):
                with self.assertRaisesRegex(ValueError, "SQLite FTS5 is not available"):
                    module.db_init(args)
            self.assertFalse(self.db_path(state_dir).exists())

    def test_db_init_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)

            self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)

            self.assertFalse([path.name for path in state_dir.iterdir() if path.name.startswith(".tmp-")])


class IndexCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_db_init(self, root, state_dir):
        return self.run_cli("db-init", "--root", str(root), "--state-dir", str(state_dir))

    def run_index(self, root, state_dir, *extra):
        return self.run_cli("index", "--root", str(root), "--state-dir", str(state_dir), *extra)

    def run_add(self, root, **kwargs):
        args = [
            "add",
            "--root",
            str(root),
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            kwargs.pop("title", "代码最少原则"),
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

    def setup_store(self, tmp):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        return root, state_dir, state_dir / "memory.sqlite"

    def memory_files(self, root):
        return sorted(path for path in (root / "memory").rglob("*.md") if path.is_file())

    def counts(self, db):
        with sqlite3.connect(db) as conn:
            return {
                "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                "memory_fts": conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0],
                "index_state": conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0],
            }

    def rows(self, db, sql, params=()):
        with sqlite3.connect(db) as conn:
            return conn.execute(sql, params).fetchall()

    def db_hash(self, db):
        return hashlib.sha256(db.read_bytes()).hexdigest()

    def replace_in_file(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_index_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.counts(db), {"memories": 0, "memory_fts": 0, "index_state": 0})
            self.assertIn("Added: 0", result.stdout)
            self.assertIn("Unchanged: 0", result.stdout)

    def test_index_adds_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, tags=["coding"]).returncode, 0)

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.counts(db), {"memories": 1, "memory_fts": 1, "index_state": 1})
            self.assertIn("Added: 1", result.stdout)

    def test_index_stores_original_memory_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(
                self.run_add(root, tags=["coding", "architecture"], content="使用尽可能少的代码实现功能。").returncode,
                0,
            )

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            [(title, content, tags)] = self.rows(db, "SELECT title, content, tags FROM memories")
            self.assertEqual(title, "代码最少原则")
            self.assertEqual(content, "使用尽可能少的代码实现功能。")
            self.assertEqual(json.loads(tags), ["coding", "architecture"])

    def test_index_chinese_bigram_fts_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            fts = self.rows(db, "SELECT id FROM memory_fts WHERE memory_fts MATCH ?", ("代码",))
            [(title,)] = self.rows(db, "SELECT title FROM memories")
            self.assertEqual(len(fts), 1)
            self.assertEqual(title, "代码最少原则")

    def test_index_english_fts_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, title="Minimal Code Principle", content="Prefer simple code.").returncode, 0)

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            self.assertEqual(len(self.rows(db, "SELECT id FROM memory_fts WHERE memory_fts MATCH ?", ("simple",))), 1)

    def test_index_is_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)

            first = self.run_index(root, state_dir)
            [(indexed_at,)] = self.rows(db, "SELECT indexed_at FROM index_state")
            second = self.run_index(root, state_dir)
            [(indexed_at_after,)] = self.rows(db, "SELECT indexed_at FROM index_state")

            self.assertIn("Added: 1", first.stdout)
            self.assertIn("Unchanged: 1", second.stdout)
            self.assertEqual(indexed_at_after, indexed_at)

    def test_index_updates_changed_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            path = self.memory_files(root)[0]
            self.replace_in_file(path, "使用尽可能少的代码实现相同功能。", "使用简单代码完成当前功能。")

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Updated: 1", result.stdout)
            [(content,)] = self.rows(db, "SELECT content FROM memories")
            self.assertEqual(content, "使用简单代码完成当前功能。")
            self.assertEqual(self.rows(db, "SELECT id, COUNT(*) FROM memory_fts GROUP BY id HAVING COUNT(*) > 1"), [])

    def test_index_deletes_removed_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            self.memory_files(root)[0].unlink()

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Deleted: 1", result.stdout)
            self.assertEqual(self.counts(db), {"memories": 0, "memory_fts": 0, "index_state": 0})

    def test_index_repairs_missing_fts_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM memory_fts")

            result = self.run_index(root, state_dir)

            self.assertIn("Updated: 1", result.stdout)
            self.assertEqual(self.counts(db)["memory_fts"], 1)

    def test_index_repairs_missing_memory_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM memories")

            result = self.run_index(root, state_dir)

            self.assertIn("Updated: 1", result.stdout)
            self.assertEqual(self.counts(db), {"memories": 1, "memory_fts": 1, "index_state": 1})

    def test_index_handles_path_change_for_same_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            old_path = self.memory_files(root)[0]
            memory_id = old_path.name.split("-", 2)[0] + "-" + old_path.name.split("-", 2)[1] + "-" + old_path.name.split("-", 2)[2].split("-", 1)[0]
            new_path = old_path.with_name(f"{memory_id}-renamed.md")
            old_relative = old_path.relative_to(root).as_posix()
            old_path.rename(new_path)

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            [(relative_path,)] = self.rows(db, "SELECT relative_path FROM memories")
            self.assertEqual(relative_path, new_path.relative_to(root).as_posix())
            self.assertEqual(self.rows(db, "SELECT relative_path FROM index_state WHERE relative_path = ?", (old_relative,)), [])
            self.assertEqual(self.rows(db, "SELECT id, COUNT(*) FROM memory_fts GROUP BY id"), [(memory_id, 1)])

    def test_index_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            before = self.db_hash(db)

            result = self.run_index(root, state_dir, "--dry-run")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Dry run: no database changes", result.stdout)
            self.assertEqual(self.db_hash(db), before)
            self.assertEqual(self.counts(db), {"memories": 0, "memory_fts": 0, "index_state": 0})

    def test_index_rejects_invalid_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.memory_files(root)[0]
            self.replace_in_file(path, 'title: "代码最少原则"\n', "")
            before = self.db_hash(db)

            result = self.run_index(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Index aborted because validation failed.", result.stderr)
            self.assertEqual(self.db_hash(db), before)

    def test_index_requires_initialized_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_index(root, state_dir)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Database is not initialized. Run db-init first.", result.stderr)
            self.assertFalse(state_dir.exists())

    def test_index_indexes_internal_and_restricted_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, title="内部原则", workspace="work", confidentiality="internal").returncode, 0)
            self.assertEqual(self.run_add(root, title="受限原则", workspace="work", confidentiality="restricted").returncode, 0)

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            self.assertEqual(self.counts(db), {"memories": 2, "memory_fts": 2, "index_state": 2})
            self.assertEqual(set(self.rows(db, "SELECT confidentiality FROM memories")), {("internal",), ("restricted",)})

    def test_index_does_not_modify_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.memory_files(root)}

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.memory_files(root)}
            self.assertEqual(after, before)

    def test_index_transaction_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_memory_module()
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            module.init_store(root)
            add_args = mock.Mock(
                root=str(root),
                type="principle",
                title="代码最少原则",
                scope="global",
                workspace="personal",
                confidentiality="personal",
                source="user",
                confidence="confirmed",
                content="使用尽可能少的代码实现功能。",
                status="active",
                context_id=None,
                project=None,
                valid_from=None,
                valid_until=None,
                tags=[],
                from_context=None,
                to_context=None,
                effective_date=None,
                reason=None,
            )
            module.add_memory(add_args)
            module.db_init(mock.Mock(root=str(root), state_dir=str(state_dir)))
            args = mock.Mock(root=str(root), state_dir=str(state_dir), dry_run=False)

            with mock.patch.object(module, "check_index_consistency", side_effect=ValueError("forced failure")):
                with self.assertRaisesRegex(ValueError, "forced failure"):
                    module.index_store(args)
            self.assertEqual(self.counts(state_dir / "memory.sqlite"), {"memories": 0, "memory_fts": 0, "index_state": 0})

    def test_index_leaves_no_duplicate_fts_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            for text in ["使用简单代码完成当前功能。", "继续使用简单代码。"]:
                self.assertEqual(self.run_index(root, state_dir).returncode, 0)
                path = self.memory_files(root)[0]
                old_content = re.search(r"content: \|-\n  .+", path.read_text(encoding="utf-8")).group(0)
                self.replace_in_file(path, old_content, f"content: |-\n  {text}")

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            self.assertEqual(self.rows(db, "SELECT id, COUNT(*) FROM memory_fts GROUP BY id HAVING COUNT(*) > 1"), [])

    def test_index_leaves_no_wal_or_shm_after_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)

            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            self.assertEqual(sorted(path.name for path in state_dir.iterdir()), ["memory.sqlite"])


class SearchCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_db_init(self, root, state_dir):
        return self.run_cli("db-init", "--root", str(root), "--state-dir", str(state_dir))

    def run_index(self, root, state_dir):
        return self.run_cli("index", "--root", str(root), "--state-dir", str(state_dir))

    def run_search(self, root, state_dir, query, *extra):
        return self.run_cli("search", query, "--root", str(root), "--state-dir", str(state_dir), *extra)

    def run_add(self, root, **kwargs):
        args = [
            "add",
            "--root",
            str(root),
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            kwargs.pop("title", "代码最少原则"),
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
            kwargs.pop("content", "使用尽可能少的代码实现相同功能。"),
            "--status",
            kwargs.pop("status", "active"),
        ]
        for option, value in kwargs.items():
            flag = "--" + option.replace("_", "-")
            if isinstance(value, list):
                args.append(flag)
                args.extend(value)
            elif value is not None:
                args.extend([flag, value])
        return self.run_cli(*args)

    def setup_store(self, tmp, index=True):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        if index:
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
        return root, state_dir, state_dir / "memory.sqlite"

    def setup_one_memory(self, tmp, **kwargs):
        root, state_dir, db = self.setup_store(tmp, index=False)
        self.assertEqual(self.run_add(root, **kwargs).returncode, 0)
        self.assertEqual(self.run_index(root, state_dir).returncode, 0)
        return root, state_dir, db

    def memory_files(self, root):
        return sorted(path for path in (root / "memory").rglob("*.md") if path.is_file())

    def db_hash(self, db):
        return hashlib.sha256(db.read_bytes()).hexdigest()

    def db_sidecars(self, db):
        return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]

    def source_hashes(self, root):
        return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.memory_files(root)}

    def replace_in_file(self, path, old, new):
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def json_search(self, root, state_dir, query, *extra):
        result = self.run_search(root, state_dir, query, "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), result.stdout

    def test_search_finds_chinese_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            result = self.run_search(root, state_dir, "代码")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Results: 1", result.stdout)
            self.assertIn("代码最少原则", result.stdout)

    def test_search_finds_long_chinese_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            result = self.run_search(root, state_dir, "代码最少原则")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Results: 1", result.stdout)

    def test_search_rejects_single_chinese_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_one_memory(tmp)
            before = self.db_hash(db)
            result = self.run_search(root, state_dir, "代")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Chinese search terms must contain at least 2 consecutive characters.", result.stderr)
            self.assertEqual(self.db_hash(db), before)

    def test_search_finds_english_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="Minimal Code Principle", content="Prefer simple code.")
            result = self.run_search(root, state_dir, "simple")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Minimal Code Principle", result.stdout)

    def test_search_returns_zero_for_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            result = self.run_search(root, state_dir, "不存在")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "Results: 0")

    def test_search_default_workspace_is_personal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="个人代码原则").returncode, 0)
            self.assertEqual(self.run_add(root, title="工作代码原则", workspace="work", confidentiality="internal").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            result = self.run_search(root, state_dir, "代码")
            self.assertIn("个人代码原则", result.stdout)
            self.assertNotIn("工作代码原则", result.stdout)

    def test_search_work_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="工作代码原则", workspace="work", confidentiality="internal")
            result = self.run_search(root, state_dir, "代码", "--workspace", "work")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("工作代码原则", result.stdout)

    def test_search_excludes_restricted_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="受限代码原则", workspace="work", confidentiality="restricted")
            result = self.run_search(root, state_dir, "代码", "--workspace", "work")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "Results: 0")

    def test_search_includes_restricted_when_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="受限代码原则", workspace="work", confidentiality="restricted")
            result = self.run_search(root, state_dir, "代码", "--workspace", "work", "--include-restricted")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("受限代码原则", result.stdout)

    def test_search_rejects_restricted_in_personal_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            result = self.run_search(root, state_dir, "代码", "--include-restricted")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--include-restricted requires --workspace work", result.stderr)

    def test_search_filters_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="原则代码", memory_type="principle").returncode, 0)
            self.assertEqual(self.run_add(root, title="会话代码", memory_type="session", scope="project", project="demo").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            result = self.run_search(root, state_dir, "代码", "--type", "session")
            self.assertIn("会话代码", result.stdout)
            self.assertNotIn("原则代码", result.stdout)

    def test_search_filters_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="Alpha 代码", memory_type="session", scope="project", project="alpha").returncode, 0)
            self.assertEqual(self.run_add(root, title="Beta 代码", memory_type="session", scope="project", project="beta").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            result = self.run_search(root, state_dir, "代码", "--project", "alpha")
            self.assertIn("Alpha 代码", result.stdout)
            self.assertNotIn("Beta 代码", result.stdout)

    def test_search_filters_context_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="情景代码", memory_type="context", scope="context", context_id="ctx-a")
            result = self.run_search(root, state_dir, "代码", "--context-id", "ctx-a")
            self.assertIn("情景代码", result.stdout)

    def test_search_default_status_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="Active 代码").returncode, 0)
            self.assertEqual(self.run_add(root, title="Historical 代码", status="historical").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            result = self.run_search(root, state_dir, "代码")
            self.assertIn("Active 代码", result.stdout)
            self.assertNotIn("Historical 代码", result.stdout)

    def test_search_include_historical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="Active 代码").returncode, 0)
            self.assertEqual(self.run_add(root, title="Historical 代码", status="historical").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            result = self.run_search(root, state_dir, "代码", "--include-historical")
            self.assertIn("Active 代码", result.stdout)
            self.assertIn("Historical 代码", result.stdout)

    def test_search_explicit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="Archived 代码", status="archived")
            result = self.run_search(root, state_dir, "代码", "--status", "archived")
            self.assertIn("Archived 代码", result.stdout)

    def test_search_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            for title in ["A 代码", "B 代码", "C 代码"]:
                self.assertEqual(self.run_add(root, title=title).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            data, _ = self.json_search(root, state_dir, "代码", "--limit", "2")
            self.assertEqual(data["count"], 2)
            self.assertEqual([row["id"] for row in data["results"]], sorted(row["id"] for row in data["results"]))

    def test_search_rejects_invalid_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            for value in ["0", "101", "not-int"]:
                result = self.run_search(root, state_dir, "代码", "--limit", value)
                self.assertNotEqual(result.returncode, 0)

    def test_search_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            data, text = self.json_search(root, state_dir, "代码")
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(data["query"], "代码")
            self.assertEqual(data["workspace"], "personal")
            self.assertEqual(data["count"], 1)
            self.assertEqual(set(data["results"][0]), {"id", "title", "type", "status", "scope", "workspace", "confidentiality", "project", "context_id", "updated", "relative_path", "score", "excerpt"})
            self.assertNotIn("content", data["results"][0])

    def test_search_json_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            first = self.run_search(root, state_dir, "代码", "--json")
            second = self.run_search(root, state_dir, "代码", "--json")
            self.assertEqual(first.stdout.encode("utf-8"), second.stdout.encode("utf-8"))

    def test_search_excerpt_uses_original_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "前缀" * 80 + "使用尽可能少的代码实现相同功能。" + "后缀" * 80
            root, state_dir, _ = self.setup_one_memory(tmp, content=content)
            data, _ = self.json_search(root, state_dir, "代码")
            excerpt = data["results"][0]["excerpt"]
            self.assertLessEqual(len(excerpt), 180)
            self.assertNotIn("代 码", excerpt)
            self.assertIn("代码", excerpt)

    def test_search_ranks_title_match_above_content_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root, title="无关标题", content="内容包含代码关键词。").returncode, 0)
            self.assertEqual(self.run_add(root, title="代码标题", content="普通内容。").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            data, _ = self.json_search(root, state_dir, "代码")
            self.assertEqual(data["results"][0]["title"], "代码标题")

    def test_search_rejects_stale_index_after_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            self.assertEqual(self.run_add(root, title="新增代码").returncode, 0)
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", result.stderr)

    def test_search_rejects_stale_index_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            self.replace_in_file(self.memory_files(root)[0], "使用尽可能少的代码实现相同功能。", "内容已经变化。")
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", result.stderr)

    def test_search_rejects_stale_index_after_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            self.memory_files(root)[0].unlink()
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", result.stderr)

    def test_search_rejects_missing_fts_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_one_memory(tmp)
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM memory_fts")
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for sidecar in self.db_sidecars(db):
                sidecar.unlink(missing_ok=True)
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", result.stderr)

    def test_search_requires_initialized_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Database is not initialized. Run db-init first.", result.stderr)

    def test_search_requires_indexed_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp, index=False)
            self.assertEqual(self.run_add(root).returncode, 0)
            result = self.run_search(root, state_dir, "代码")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", result.stderr)

    def test_search_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            result = self.run_search(root, state_dir, "code")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "Results: 0")

    def test_search_does_not_modify_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_one_memory(tmp)
            before_hash = self.db_hash(db)
            before_mtime = db.stat().st_mtime_ns
            self.assertFalse(any(path.exists() for path in self.db_sidecars(db)))
            result = self.run_search(root, state_dir, "代码")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.db_hash(db), before_hash)
            self.assertEqual(db.stat().st_mtime_ns, before_mtime)
            self.assertFalse(any(path.exists() for path in self.db_sidecars(db)))

    def test_search_does_not_modify_source_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp)
            before = self.source_hashes(root)
            self.assertEqual(self.run_search(root, state_dir, "代码").returncode, 0)
            self.assertEqual(self.source_hashes(root), before)

    def test_search_escapes_fts_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_one_memory(tmp, title="OR NEAR star 代码", content="safe query text")
            result = self.run_search(root, state_dir, '" OR (NEAR*) 代码')
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_search_recall_benchmark_on_medium_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_memory_module()
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            module.init_store(root)
            for index in range(80):
                module.add_memory(
                    mock.Mock(
                        root=str(root),
                        type="principle",
                        title=f"Benchmark memory {index}",
                        scope="global",
                        workspace="personal",
                        confidentiality="personal",
                        source="test",
                        confidence="confirmed",
                        content="ordinary searchable content",
                        status="active",
                        context_id=None,
                        project=None,
                        valid_from=None,
                        valid_until=None,
                        tags=[],
                        from_context=None,
                        to_context=None,
                        effective_date=None,
                        reason=None,
                    )
                )
            module.add_memory(
                mock.Mock(
                    root=str(root),
                    type="principle",
                    title="Benchmark target",
                    scope="global",
                    workspace="personal",
                    confidentiality="personal",
                    source="test",
                    confidence="confirmed",
                    content="unique recall benchmark needle",
                    status="active",
                    context_id=None,
                    project=None,
                    valid_from=None,
                    valid_until=None,
                    tags=[],
                    from_context=None,
                    to_context=None,
                    effective_date=None,
                    reason=None,
                )
            )
            module.db_init(mock.Mock(root=str(root), state_dir=str(state_dir)))
            module.index_store(mock.Mock(root=str(root), state_dir=str(state_dir), dry_run=False))

            started = time.perf_counter()
            summary = module.search_store(
                mock.Mock(
                    root=str(root),
                    state_dir=str(state_dir),
                    query="unique recall benchmark needle",
                    workspace="personal",
                    type=None,
                    project=None,
                    context_id=None,
                    status=None,
                    include_historical=False,
                    include_restricted=False,
                    limit=5,
                )
            )
            elapsed = time.perf_counter() - started

            self.assertEqual(summary["count"], 1)
            self.assertEqual(summary["results"][0]["title"], "Benchmark target")
            self.assertLess(elapsed, 2.0)


class ExportCommandTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_init(self, root):
        return self.run_cli("init", "--root", str(root))

    def run_export(self, root, *extra):
        return self.run_cli("export", "--root", str(root), *extra)

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

    def load_jsonl(self, root):
        path = root / "exports" / "memory.jsonl"
        text = path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def load_manifest(self, root):
        return json.loads((root / "exports" / "index_manifest.json").read_text(encoding="utf-8"))

    def test_export_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)

            result = self.run_export(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "exports" / "memory.jsonl").read_text(encoding="utf-8"), "")
            self.assertEqual(self.load_manifest(root), {"format_version": 1, "records": []})
            self.assertIn("Exported: 0 records", result.stdout)

    def test_export_valid_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, tags=["coding"]).returncode, 0)
            source = self.memory_files(root)[0]
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            result = self.run_export(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = self.load_jsonl(root)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(set(row), {"record", "relative_path", "sha256"})
            self.assertEqual(row["sha256"], source_hash)
            self.assertEqual(row["relative_path"], source.relative_to(root).as_posix())
            self.assertEqual(row["record"]["type"], "principle")
            manifest_row = self.load_manifest(root)["records"][0]
            self.assertEqual(manifest_row["id"], row["record"]["id"])
            self.assertEqual(manifest_row["type"], "principle")
            self.assertEqual(manifest_row["status"], "active")
            self.assertEqual(manifest_row["workspace"], "personal")
            self.assertEqual(manifest_row["confidentiality"], "personal")
            self.assertEqual(manifest_row["relative_path"], row["relative_path"])
            self.assertEqual(manifest_row["sha256"], source_hash)

    def test_export_order_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, "session", "会话", "project", project="p").returncode, 0)
            self.assertEqual(self.run_add(root, "principle", "原则", "global").returncode, 0)
            self.assertEqual(self.run_add(root, "profile", "档案", "global").returncode, 0)

            self.assertEqual(self.run_export(root).returncode, 0)

            paths = [row["relative_path"] for row in self.load_jsonl(root)]
            self.assertEqual(paths, sorted(paths))

    def test_export_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_export(root).returncode, 0)
            jsonl_before = (root / "exports" / "memory.jsonl").read_bytes()
            manifest_before = (root / "exports" / "index_manifest.json").read_bytes()

            self.assertEqual(self.run_export(root).returncode, 0)

            self.assertEqual((root / "exports" / "memory.jsonl").read_bytes(), jsonl_before)
            self.assertEqual((root / "exports" / "index_manifest.json").read_bytes(), manifest_before)

    def test_export_rejects_invalid_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            path = self.memory_files(root)[0]
            text = path.read_text(encoding="utf-8")
            path.write_text(re.sub(r'^title: ".*"\n', "", text, count=1, flags=re.MULTILINE), encoding="utf-8")

            result = self.run_export(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Export aborted because validation failed.", result.stdout)
            self.assertFalse((root / "exports" / "memory.jsonl").exists())

    def test_export_preserves_existing_outputs_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_export(root).returncode, 0)
            jsonl_before = (root / "exports" / "memory.jsonl").read_bytes()
            manifest_before = (root / "exports" / "index_manifest.json").read_bytes()
            path = self.memory_files(root)[0]
            path.write_text(path.read_text(encoding="utf-8").replace('type: "principle"', 'type: "bad"', 1), encoding="utf-8")

            result = self.run_export(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "exports" / "memory.jsonl").read_bytes(), jsonl_before)
            self.assertEqual((root / "exports" / "index_manifest.json").read_bytes(), manifest_before)

    def test_export_excludes_internal_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, workspace="work", confidentiality="internal").returncode,
                0,
            )

            result = self.run_export(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.load_jsonl(root), [])
            self.assertIn("Skipped internal: 1", result.stdout)

    def test_export_includes_internal_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, workspace="work", confidentiality="internal").returncode,
                0,
            )

            result = self.run_export(root, "--include-internal")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = self.load_jsonl(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["record"]["confidentiality"], "internal")

    def test_export_never_exports_restricted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(
                self.run_add(root, workspace="work", confidentiality="restricted").returncode,
                0,
            )

            default = self.run_export(root)
            include_internal = self.run_export(root, "--include-internal")

            self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
            self.assertEqual(include_internal.returncode, 0, include_internal.stdout + include_internal.stderr)
            self.assertEqual(self.load_jsonl(root), [])
            self.assertIn("Skipped restricted: 1", default.stdout)
            self.assertIn("Skipped restricted: 1", include_internal.stdout)

    def test_export_does_not_modify_memory_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.memory_files(root)}

            self.assertEqual(self.run_export(root).returncode, 0)

            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.memory_files(root)}
            self.assertEqual(before, after)

    def test_export_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root).returncode, 0)

            self.assertEqual(self.run_export(root).returncode, 0)

            names = [path.name for path in (root / "exports").iterdir()]
            self.assertFalse([name for name in names if name.startswith(".tmp-")])

    def test_export_invalid_root_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            root.mkdir()

            result = self.run_export(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "exports" / "memory.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
