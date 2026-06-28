import argparse
import json
import hashlib
import importlib.util
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
DISTILL_CLI = REPO_ROOT / "src" / "memory_distill.py"
CSV_HEADER = "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags"


def load_memory_module():
    spec = importlib.util.spec_from_file_location("memory_module_for_tests", MEMORY_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finish_add_fixture(root, result, status="active"):
    if result.returncode != 0:
        return result
    memory_id = re.search(r"memory_id: (.+)", result.stdout).group(1)
    if status == "candidate":
        return result
    if status != "active":
        for path in (Path(root) / "memory").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if f'id: "{memory_id}"' in text:
                path.write_text(text.replace('status: "candidate"', f'status: "{status}"', 1), encoding="utf-8")
                return result
        raise AssertionError(f"missing fixture memory {memory_id}")
    state = Path(root).parent / "state"
    if not (state / "memory.sqlite").is_file():
        return result
    return subprocess.run(
        [sys.executable, str(DISTILL_CLI), "accept", "--root", str(root), "--state-dir", str(state), "--id", memory_id],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )

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


@contextmanager
def sqlite_connection(path):
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            yield conn


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
            "--confirmed",
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
            self.assertIn(created.relative_to(root).as_posix(), result.stdout)

    def test_add_defaults_to_candidate_without_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            result = self.run_cli(
                "add", "--root", str(root), "--type", "principle", "--title", "Candidate default",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "codex", "--confidence", "inferred", "--content", "candidate content",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = self.memory_files(root)[0].read_text(encoding="utf-8")
            self.assertIn('status: "candidate"', text)

    def test_add_confirmed_remains_candidate_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            result = self.run_add(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = self.memory_files(root)[0].read_text(encoding="utf-8")
            self.assertIn('status: "candidate"', text)
            self.assertIn('source: "user"', text)
            self.assertNotIn('source: "manual:user_confirmed"', text)
            self.assertIn("Review/accept required", result.stdout)

    def test_add_confirmed_codex_cannot_create_active_or_rewrite_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            result = self.run_cli(
                "add", "--confirmed", "--root", str(root), "--type", "principle",
                "--title", "Codex proposal", "--scope", "global", "--workspace", "personal",
                "--confidentiality", "personal", "--source", "codex", "--confidence", "confirmed",
                "--content", "proposal content", "--status", "active",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            text = self.memory_files(root)[0].read_text(encoding="utf-8")
            self.assertIn('status: "candidate"', text)
            self.assertIn('source: "codex"', text)
            self.assertNotIn('status: "active"', text)
            self.assertNotIn('source: "manual:user_confirmed"', text)

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
            "--confirmed",
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
            line = re.search(r'^created: "\d{4}-\d{2}-\d{2}"\n', path.read_text(encoding="utf-8"), re.MULTILINE).group(0)
            self.replace_in_file(path, line, 'created: "2026-02-30"\n')

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
            for path in self.memory_files(root):
                self.replace_in_file(path, 'status: "candidate"', 'status: "active"')

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
        module = load_memory_module()
        memory_id = f"context-fixture-{hashlib.sha256(context_id.encode()).hexdigest()[:8]}"
        path = root / "memory/contexts" / f"{memory_id}-{module.safe_slug(title)}.md"
        record = {
            "id": memory_id, "type": "context", "title": title,
            "created": "2026-06-27", "updated": "2026-06-27", "status": status,
            "scope": "context", "workspace": workspace, "confidentiality": confidentiality,
            "source": "test:fixture", "confidence": "confirmed", "content": content,
            "context_id": context_id, "valid_from": valid_from,
            "tags": ["education", "research"],
        }
        module.atomic_write_text(path, module.render_memory(record))
        return subprocess.CompletedProcess([], 0, "", "")

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

    def test_context_transition_creates_one_candidate_without_changing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
            source_path, source = self.record_by_context(root, "university-student")
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

            result = self.run_transition(
                root, "--source", "codex", "--confidence", "inferred",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)
            self.assertEqual(self.record_by_context(root, "university-student")[1]["status"], "active")
            self.assertEqual(len(self.records(root)), 2)
            [(candidate_path, candidate)] = self.transition_records(root)
            self.assertEqual(candidate["status"], "candidate")
            self.assertEqual(candidate["audit_status"], "awaiting_review")
            self.assertEqual(candidate["source"], "codex")
            self.assertEqual(candidate["confidence"], "inferred")
            self.assertEqual(candidate["target_id"], source["id"])
            self.assertEqual(candidate["expected_target_status"], "active")
            self.assertEqual(candidate["expected_target_sha256"], source_hash)
            self.assertEqual(len(candidate["proposal_input_hash"]), 64)
            new_record = json.loads(candidate["new_record_json"])
            self.assertEqual(new_record["context_id"], "industry-engineer")
            self.assertEqual(new_record["status"], "active")
            self.assertIn(candidate_path.relative_to(root).as_posix(), result.stdout)

    def test_context_transition_same_workspace_leaves_source_active_and_creates_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            result = self.run_transition(root, "--workspace", "personal", "--confidentiality", "personal")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source_path, source = self.record_by_context(root, "university-student")
            transitions = self.transition_records(root)
            self.assertEqual(source["status"], "active")
            self.assertNotIn("valid_until", source)
            self.assertEqual([record for _, record in self.records(root) if record.get("context_id") == "industry-engineer"], [])
            self.assertEqual(len(transitions), 1)
            self.assertEqual(transitions[0][1]["status"], "candidate")
            self.assertIn(f"Source unchanged: {source_path.relative_to(root).as_posix()}", result.stdout)

    def test_context_transition_cross_workspace_records_planned_work_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            result = self.run_transition(root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            [( _, candidate)] = self.transition_records(root)
            target = json.loads(candidate["new_record_json"])
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

    def test_context_transition_candidate_records_planned_supersession(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)

            self.assertEqual(self.run_transition(root).returncode, 0)

            _, source = self.record_by_context(root, "university-student")
            [(_, candidate)] = self.transition_records(root)
            target = json.loads(candidate["new_record_json"])
            self.assertNotIn("superseded_by", source)
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

    def test_context_transition_candidate_write_failure_keeps_source_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            module = load_memory_module()
            self.assertEqual(module.init_store(root)[0], root.resolve())
            self.assertEqual(self.run_add_context(root, "university-student").returncode, 0)
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

            original_replace = module.os.replace
            calls = 0
            failed = False

            def fail_first_replace(path, target):
                nonlocal calls, failed
                if Path(path).name.startswith(".tmp-"):
                    calls += 1
                if calls == 1 and not failed:
                    failed = True
                    raise OSError("disk full")
                return original_replace(path, target)

            with mock.patch.object(module.os, "replace", side_effect=fail_first_replace):
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
            "--confirmed",
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
        with sqlite_connection(db) as conn:
            return dict(
                conn.execute(
                    """
                    SELECT name, type
                    FROM sqlite_master
                    WHERE name IN (
                        'memories', 'memory_fts', 'index_state',
                        'documents', 'document_fts', 'document_index_state'
                    )
                    """
                ).fetchall()
            )

    def columns(self, db, table):
        with sqlite_connection(db) as conn:
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

            with sqlite_connection(db) as conn:
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

            self.assertEqual(version, 3)
            self.assertEqual(mode.lower(), "wal")
            self.assertEqual(
                objects,
                {
                    "memories": "table",
                    "memory_fts": "table",
                    "index_state": "table",
                    "documents": "table",
                    "document_fts": "table",
                    "document_index_state": "table",
                },
            )
            self.assertEqual(
                self.columns(db, "memories"),
                [
                    "id",
                    "type",
                    "title",
                    "content",
                    "tags",
                    "status",
                    "scope",
                    "workspace",
                    "confidentiality",
                    "source",
                    "confidence",
                    "context_id",
                    "project",
                    "valid_from",
                    "valid_until",
                    "created",
                    "updated",
                    "relative_path",
                    "sha256",
                ],
            )
            self.assertEqual(
                self.columns(db, "index_state"),
                ["relative_path", "sha256", "mtime_ns", "indexed_at"],
            )
            self.assertEqual(
                self.columns(db, "documents"),
                [
                    "id",
                    "source_kind",
                    "source_id",
                    "title",
                    "content",
                    "workspace",
                    "confidentiality",
                    "project",
                    "context_id",
                    "updated",
                    "relative_path",
                    "sha256",
                    "metadata_json",
                ],
            )
            self.assertEqual(
                self.columns(db, "document_index_state"),
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
            try:
                state_dir.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 1314:
                    raise
                self.skipTest(f"symbolic links unavailable: {exc}")
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
            try:
                self.db_path(state_dir).symlink_to(target)
            except OSError as exc:
                if getattr(exc, "winerror", None) != 1314:
                    raise
                self.skipTest(f"symbolic links unavailable: {exc}")
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
            with sqlite_connection(db) as conn:
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
            with sqlite_connection(db) as conn:
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
            with sqlite_connection(db) as conn:
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
            "--confirmed",
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
        with sqlite_connection(db) as conn:
            return {
                "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
                "memory_fts": conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0],
                "index_state": conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0],
            }

    def rows(self, db, sql, params=()):
        with sqlite_connection(db) as conn:
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
            with sqlite_connection(db) as conn:
                conn.execute("DELETE FROM memory_fts")

            result = self.run_index(root, state_dir)

            self.assertIn("Updated: 1", result.stdout)
            self.assertEqual(self.counts(db)["memory_fts"], 1)

    def test_index_repairs_missing_memory_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            with sqlite_connection(db) as conn:
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


class UnifiedSearchCommandTests(unittest.TestCase):
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

    def run_db_rebuild(self, root, state_dir):
        return self.run_cli("db-rebuild", "--root", str(root), "--state-dir", str(state_dir))

    def run_index(self, root, state_dir, *extra):
        return self.run_cli("index", "--root", str(root), "--state-dir", str(state_dir), *extra)

    def run_search(self, root, state_dir, query, *extra):
        return self.run_cli(
            "search",
            query,
            "--root",
            str(root),
            "--state-dir",
            str(state_dir),
            "--json",
            *extra,
        )

    def run_doctor(self, root, state_dir):
        return self.run_cli("doctor", "--root", str(root), "--state-dir", str(state_dir), "--json")

    def run_add(self, root, **kwargs):
        status = kwargs.pop("status", "active")
        args = [
            "add",
            "--confirmed",
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
        return finish_add_fixture(root, self.run_cli(*args), status)

    def setup_store(self, tmp):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        return root, state_dir, state_dir / "memory.sqlite"

    def rows(self, db, sql, params=()):
        with sqlite_connection(db) as conn:
            return conn.execute(sql, params).fetchall()

    def write_document(self, root, relative, body, front_matter=None):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if front_matter:
            lines = ["---"]
            for key, value in front_matter.items():
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            lines.extend(["---", "", body])
            path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        else:
            path.write_text(body, encoding="utf-8")
        return path

    def test_db_init_creates_schema_v3_document_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _, db = self.setup_store(tmp)

            [(version,)] = self.rows(db, "PRAGMA user_version")
            tables = {name for (name,) in self.rows(db, "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')")}

            self.assertEqual(version, 3)
            self.assertIn("documents", tables)
            self.assertIn("document_fts", tables)
            self.assertIn("document_index_state", tables)

    def test_db_rebuild_creates_schema_v3_and_indexes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, title="代码最少原则", content="使用尽可能少的代码实现相同功能。").returncode, 0)
            self.write_document(root, "imports/chatgpt/conversations/2026/06/thread.md", "PDC raw conversation evidence")

            result = self.run_db_rebuild(root, state_dir)
            db = state_dir / "memory.sqlite"

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Database rebuilt", result.stdout)
            [(version,)] = self.rows(db, "PRAGMA user_version")
            self.assertEqual(version, 3)
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM memories"), [(1,)])
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM documents"), [(1,)])

    def test_index_adds_memory_and_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.assertEqual(self.run_add(root).returncode, 0)
            self.write_document(root, "imports/chatgpt/conversations/2026/06/thread.md", "PDC raw conversation evidence")
            self.write_document(root, "imports/manual/raw/note.txt", "Manual raw note about PDC review cadence")
            self.write_document(root, "literature/notes/pdc-note.md", "PDC literature note for target journal", {"project": "ljq"})
            self.write_document(root, "manuscripts/current/paper.md", "Manuscript sentence about impact angle", {"project": "ljq"})

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM memories"), [(1,)])
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM documents"), [(4,)])
            self.assertIn("Documents added: 4", result.stdout)

    def test_search_defaults_to_memory_and_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, title="PDC principle", content="PDC evidence must be traceable.").returncode, 0)
            self.write_document(root, "manuscripts/current/pdc-paper.md", "PDC manuscript sentence with traceable evidence")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            result = self.run_search(root, state_dir, "PDC")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(result.stdout)["results"]
            kinds = {row["kind"] for row in rows}
            self.assertIn("memory", kinds)
            self.assertIn("document", kinds)
            self.assertTrue(all(row["confidentiality"] != "restricted" for row in rows))

    def test_search_supports_kind_source_and_project_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.write_document(root, "literature/notes/pdc-note.md", "PDC literature note", {"project": "ljq"})
            self.write_document(root, "manuscripts/current/pdc-paper.md", "PDC manuscript sentence", {"project": "other"})
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            result = self.run_search(root, state_dir, "PDC", "--kind", "document", "--source-kind", "literature", "--project", "ljq")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(result.stdout)["results"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "document")
            self.assertEqual(rows[0]["source_kind"], "literature")
            self.assertEqual(rows[0]["project"], "ljq")

    def test_search_does_not_return_restricted_documents_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.write_document(root, "manuscripts/current/public.md", "PDC public manuscript", {"project": "ljq"})
            self.write_document(
                root,
                "manuscripts/evidence/restricted.md",
                "PDC restricted evidence",
                {"workspace": "work", "confidentiality": "restricted", "project": "ljq"},
            )
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            result = self.run_search(root, state_dir, "PDC", "--kind", "document", "--project", "ljq")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(result.stdout)["results"]
            self.assertEqual([row["relative_path"] for row in rows], ["manuscripts/current/public.md"])

    def test_search_enforces_inactive_restricted_and_project_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            common = {"content": "BoundarySafetyProbe durable evidence"}
            projects = [
                self.run_add(root, memory_type="project", title="PDC project", project="pdc", scope="project"),
                self.run_add(root, memory_type="project", title="Other project", project="other", scope="project"),
            ]
            self.assertTrue(all(result.returncode == 0 for result in projects), [result.stderr for result in projects])
            additions = [
                self.run_add(root, title="global active", **common),
                self.run_add(root, title="global restricted", confidentiality="restricted", workspace="work", **common),
                self.run_add(root, title="global deprecated", status="deprecated", **common),
                self.run_add(root, title="pdc active", project="pdc", scope="project", **common),
                self.run_add(root, title="other active", project="other", scope="project", **common),
            ]
            self.assertTrue(all(result.returncode == 0 for result in additions), [result.stderr for result in additions])
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            default = self.run_search(root, state_dir, "BoundarySafetyProbe", "--kind", "memory")
            self.assertEqual(default.returncode, 0, default.stdout + default.stderr)
            self.assertEqual({row["title"] for row in json.loads(default.stdout)["results"]}, {"global active"})

            project = self.run_search(root, state_dir, "BoundarySafetyProbe", "--kind", "memory", "--project", "pdc")
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            self.assertEqual({row["title"] for row in json.loads(project.stdout)["results"]}, {"global active", "pdc active"})

            opted_in = self.run_search(
                root, state_dir, "BoundarySafetyProbe", "--kind", "memory",
                "--include-restricted", "--include-inactive",
            )
            self.assertEqual(opted_in.returncode, 0, opted_in.stdout + opted_in.stderr)
            self.assertEqual(
                {row["title"] for row in json.loads(opted_in.stdout)["results"]},
                {"global active", "global restricted", "global deprecated"},
            )

    def test_search_supports_manual_text_chatgpt_literature_and_manuscript_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            self.write_document(
                root,
                "imports/chatgpt/conversations/2026/06/thread.md",
                "PDC ChatGPT archive discussion",
                {"conversation_id": "thread-1", "title": "Archive Thread"},
            )
            self.write_document(root, "imports/manual/text/note.md", "PDC manual text note")
            self.write_document(root, "literature/notes/pdc-note.md", "PDC literature note")
            self.write_document(root, "manuscripts/current/paper.md", "PDC manuscript sentence")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            for source_kind, expected_path in [
                ("chatgpt", "imports/chatgpt/conversations/2026/06/thread.md"),
                ("manual", "imports/manual/text/note.md"),
                ("literature", "literature/notes/pdc-note.md"),
                ("manuscript", "manuscripts/current/paper.md"),
            ]:
                with self.subTest(source_kind=source_kind):
                    result = self.run_search(root, state_dir, "PDC", "--kind", "document", "--source-kind", source_kind)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    rows = json.loads(result.stdout)["results"]
                    self.assertEqual([row["relative_path"] for row in rows], [expected_path])

    def test_manual_text_sidecar_wins_over_raw_and_binary_raw_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            self.write_document(root, "imports/manual/raw/2026/06/abcd-note.txt", "PDC duplicate raw")
            self.write_document(
                root,
                "imports/manual/text/2026/06/abcd-note.md",
                "PDC duplicate text",
                {
                    "source_path": "imports/manual/raw/2026/06/abcd-note.txt",
                    "source_sha256": "0" * 64,
                    "original_name": "note.txt",
                    "media_type": "text/plain",
                    "extractor": "utf-8",
                    "imported_at": "2026-06-24T00:00:00+00:00",
                },
            )
            binary = root / "imports/manual/raw/2026/06/file.pdf"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"%PDF-\xff\x00PDC")

            result = self.run_index(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.rows(db, "SELECT relative_path FROM documents ORDER BY relative_path"), [("imports/manual/text/2026/06/abcd-note.md",)])
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM document_fts"), [(1,)])
            metadata = json.loads(self.rows(db, "SELECT metadata_json FROM documents")[0][0])
            self.assertEqual(metadata["source_path"], "imports/manual/raw/2026/06/abcd-note.txt")

    def test_index_tracks_document_update_delete_and_avoids_duplicate_fts_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, db = self.setup_store(tmp)
            note = self.write_document(root, "imports/manual/raw/note.txt", "PDC first version")

            first = self.run_index(root, state_dir)
            second = self.run_index(root, state_dir)

            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("Documents added: 1", first.stdout)
            self.assertIn("Documents unchanged: 1", second.stdout)
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM document_fts"), [(1,)])

            note.write_text("PDC second version", encoding="utf-8")
            updated = self.run_index(root, state_dir)
            self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
            self.assertIn("Documents updated: 1", updated.stdout)
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM document_fts"), [(1,)])

            note.unlink()
            deleted = self.run_index(root, state_dir)
            self.assertEqual(deleted.returncode, 0, deleted.stdout + deleted.stderr)
            self.assertIn("Documents deleted: 1", deleted.stdout)
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM documents"), [(0,)])
            self.assertEqual(self.rows(db, "SELECT COUNT(*) FROM document_fts"), [(0,)])

    def test_search_supports_workspace_filter_and_rejects_stale_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            doc = self.write_document(root, "manuscripts/current/work.md", "PDC work manuscript", {"workspace": "work"})
            self.write_document(root, "manuscripts/current/personal.md", "PDC personal manuscript", {"workspace": "personal"})
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            filtered = self.run_search(root, state_dir, "PDC", "--kind", "document", "--workspace", "work")

            self.assertEqual(filtered.returncode, 0, filtered.stdout + filtered.stderr)
            rows = json.loads(filtered.stdout)["results"]
            self.assertEqual([row["relative_path"] for row in rows], ["manuscripts/current/work.md"])

            doc.write_text("PDC changed work manuscript", encoding="utf-8")
            stale = self.run_search(root, state_dir, "PDC", "--kind", "document")

            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("Search aborted because the index is stale. Run index first.", stale.stderr)

    def test_doctor_reports_stale_index_hash_mismatch_and_manual_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir, _ = self.setup_store(tmp)
            doc = self.write_document(root, "imports/manual/raw/note.txt", "PDC raw note")
            self.write_document(root, "imports/manual/text/orphan.md", "PDC orphan text")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            doc.write_text("PDC changed raw note", encoding="utf-8")

            result = self.run_doctor(root, state_dir)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["memory_index_fresh"])
            self.assertFalse(summary["document_index_fresh"])
            self.assertEqual(summary["manual_orphan_raw"], [])
            self.assertEqual(summary["manual_orphan_text"], ["orphan"])
            self.assertEqual(summary["raw_files"], 1)
            self.assertEqual(summary["supported_documents"], 2)
            self.assertEqual(summary["hash_mismatches"], [{"kind": "document", "relative_path": "imports/manual/raw/note.txt"}])


class ProjectGovernanceCommandTests(unittest.TestCase):
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

    def run_add(self, root, memory_type="principle", title="记忆", scope="global", content="内容", **kwargs):
        status = kwargs.pop("status", "active")
        args = [
            "add",
            "--confirmed",
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
            kwargs.pop("confidence", "confirmed"),
            "--content",
            content,
        ]
        for option, value in kwargs.items():
            if value is not None:
                args.extend(["--" + option.replace("_", "-"), value])
        return finish_add_fixture(root, self.run_cli(*args), status)

    def setup_store(self, tmp):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        return root, state_dir

    def search(self, root, state_dir, query, *extra):
        result = self.run_cli("search", query, "--root", str(root), "--state-dir", str(state_dir), "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["results"]

    def write_document(self, root, relative, text):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_project_scoped_memory_requires_registered_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.setup_store(tmp)

            missing = self.run_add(root, title="项目事实", scope="project", project="pdc")

            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("project does not reference an active project", missing.stderr)

            project = self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc")
            self.assertEqual(project.returncode, 0, project.stdout + project.stderr)
            fact = self.run_add(root, title="项目事实", scope="project", project="pdc")
            self.assertEqual(fact.returncode, 0, fact.stdout + fact.stderr)

    def test_rejects_multiple_active_project_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc").returncode, 0)

            duplicate = self.run_add(root, memory_type="project", title="PDC 项目 2", scope="project", project="pdc")

            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("multiple active project records", duplicate.stderr)

    def test_document_metadata_controls_project_and_workspace_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.write_document(root, "imports/manual/raw/note.txt", "PDC metadata controlled document")
            result = self.run_cli(
                "document-meta",
                "set",
                "--root",
                str(root),
                "--path",
                "imports/manual/raw/note.txt",
                "--project",
                "pdc",
                "--workspace",
                "work",
                "--confidentiality",
                "internal",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            personal = self.search(root, state_dir, "PDC", "--kind", "document", "--workspace", "personal")
            work = self.search(root, state_dir, "PDC", "--kind", "document", "--workspace", "work", "--project", "pdc")

            self.assertEqual(personal, [])
            self.assertEqual([row["relative_path"] for row in work], ["imports/manual/raw/note.txt"])

    def test_document_metadata_changes_are_reindexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.write_document(root, "imports/manual/text/note.md", "PDC metadata reindex document")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            self.assertEqual(
                [row["relative_path"] for row in self.search(root, state_dir, "PDC", "--kind", "document", "--project", "pdc")],
                ["imports/manual/text/note.md"],
            )

            result = self.run_cli(
                "document-meta",
                "set",
                "--root",
                str(root),
                "--path",
                "imports/manual/text/note.md",
                "--project",
                "pdc",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            self.assertEqual(
                [row["relative_path"] for row in self.search(root, state_dir, "PDC", "--kind", "document", "--project", "pdc")],
                ["imports/manual/text/note.md"],
            )

            result = self.run_cli("document-meta", "unset", "--root", str(root), "--path", "imports/manual/text/note.md")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            self.assertEqual(
                [row["relative_path"] for row in self.search(root, state_dir, "PDC", "--kind", "document", "--project", "pdc")],
                ["imports/manual/text/note.md"],
            )

    def test_search_as_of_context_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, memory_type="context", title="Context A", scope="context", context_id="ctx-a", content="ctx").returncode, 0)
            self.assertEqual(self.run_add(root, memory_type="context", title="Context B", scope="context", context_id="ctx-b", content="ctx", status="historical").returncode, 0)
            self.assertEqual(self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc").returncode, 0)
            self.assertEqual(self.run_add(root, memory_type="project", title="Other 项目", scope="project", project="other").returncode, 0)
            self.assertEqual(self.run_add(root, title="Global principle", content="PDC shared principle").returncode, 0)
            self.assertEqual(self.run_add(root, title="PDC current", scope="project", project="pdc", content="PDC scoped current", valid_from="2026-01-01", valid_until="2026-12-31").returncode, 0)
            self.assertEqual(self.run_add(root, title="Other current", scope="project", project="other", content="PDC other project").returncode, 0)
            self.assertEqual(self.run_add(root, title="Context A fact", scope="context", context_id="ctx-a", content="PDC context A").returncode, 0)
            self.assertEqual(self.run_add(root, title="Context B fact", scope="context", context_id="ctx-b", content="PDC context B").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            rows = self.search(root, state_dir, "PDC", "--project", "pdc", "--context-id", "ctx-a", "--as-of", "2026-06-01")
            paths = {row["relative_path"] for row in rows}

            self.assertTrue(any(row["temporal_status"] == "current" for row in rows))
            self.assertTrue(any(row["scope"] == "global" for row in rows if row["kind"] == "memory"))
            self.assertFalse(any("other" in (row["project"] or "") for row in rows))
            self.assertFalse(any(row.get("context_id") == "ctx-b" for row in rows))
            self.assertFalse(any("Other current" in row["title"] for row in rows))
            self.assertFalse(any("Context B fact" in row["title"] for row in rows))

            expired = self.search(root, state_dir, "PDC scoped current", "--project", "pdc", "--as-of", "2027-01-01")
            self.assertEqual(expired, [])

    def test_project_status_counts_project_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.assertEqual(self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc").returncode, 0)
            self.assertEqual(self.run_add(root, title="PDC fact", scope="project", project="pdc", content="PDC fact", valid_until="2025-01-01").returncode, 0)
            self.write_document(root, "imports/manual/raw/pdc.txt", "PDC doc")
            self.write_document(root, "imports/manual/raw/unassigned.txt", "Unassigned doc")
            self.assertEqual(self.run_cli("document-meta", "set", "--root", str(root), "--path", "imports/manual/raw/pdc.txt", "--project", "pdc").returncode, 0)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            result = self.run_cli("project-status", "--root", str(root), "--state-dir", str(state_dir), "--project", "pdc", "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["project"], "pdc")
            self.assertEqual(summary["project_memories"], 2)
            self.assertEqual(summary["documents"], 1)
            self.assertEqual(summary["unassigned_documents"], 1)
            self.assertEqual(summary["expired"], 1)


class ContextPackCommandTests(unittest.TestCase):
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

    def run_add(self, root, memory_type="principle", title="记忆", scope="global", content="内容", **kwargs):
        args = [
            "add",
            "--confirmed",
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
            kwargs.pop("confidence", "confirmed"),
            "--content",
            content,
        ]
        for option, value in kwargs.items():
            if value is not None:
                args.extend(["--" + option.replace("_", "-"), value])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        accepted = finish_add_fixture(root, result)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        return re.search(r"memory_id: (.+)", result.stdout).group(1)

    def setup_store(self, tmp):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        return root, state_dir

    def write_document(self, root, relative, text, front_matter=None):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if front_matter:
            lines = ["---"]
            for key, value in front_matter.items():
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            lines.extend(["---", "", text])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def context_pack(self, root, state_dir, query, *extra):
        result = self.run_cli("context", query, "--root", str(root), "--state-dir", str(state_dir), "--format", "json", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_context_pack_schema_order_and_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc", content="PDC project skeleton")
            self.run_add(root, title="Global principle", content="PDC global principle")
            self.run_add(root, memory_type="decision", title="Project decision", scope="project", project="pdc", content="PDC project decision")
            self.write_document(root, "manuscripts/current/pdc.md", "PDC manuscript evidence", {"project": "pdc"})
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            pack = self.context_pack(root, state_dir, "PDC", "--project", "pdc", "--workspace", "personal")

            self.assertEqual(list(pack)[:4], ["schema", "query", "generated_at", "workspace"])
            self.assertEqual(pack["schema"], "context-pack/v1")
            self.assertEqual(pack["project"], "pdc")
            self.assertTrue(pack["project_memories"])
            self.assertTrue(pack["query_memories"])
            self.assertTrue(pack["evidence_documents"])
            self.assertTrue(all("id" in source and "source_path" in source for source in pack["sources"]))

    def test_context_pack_has_zero_project_and_restricted_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, memory_type="project", title="PDC 项目", scope="project", project="pdc", content="PDC")
            self.run_add(root, memory_type="project", title="Other 项目", scope="project", project="other", content="PDC")
            self.run_add(root, title="PDC keep", scope="project", project="pdc", content="PDC keep")
            self.run_add(root, title="PDC other", scope="project", project="other", content="PDC leak")
            self.write_document(root, "manuscripts/current/pdc.md", "PDC visible evidence", {"project": "pdc"})
            self.write_document(root, "manuscripts/evidence/restricted.md", "PDC restricted evidence", {"project": "pdc", "workspace": "work", "confidentiality": "restricted"})
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            pack = self.context_pack(root, state_dir, "PDC", "--project", "pdc", "--workspace", "personal")
            text = json.dumps(pack, ensure_ascii=False)

            self.assertNotIn("PDC leak", text)
            self.assertNotIn("restricted evidence", text)

    def test_context_pack_rejects_stale_index_and_respects_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, title="Budget", content="PDC " + "x" * 1000)
            doc = self.write_document(root, "imports/manual/raw/note.txt", "PDC " + "y" * 1000)
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            small = self.run_cli("context", "PDC", "--root", str(root), "--state-dir", str(state_dir), "--format", "json", "--max-chars", "900")
            self.assertEqual(small.returncode, 0, small.stdout + small.stderr)
            self.assertLessEqual(len(small.stdout), 900)
            self.assertTrue(json.loads(small.stdout)["truncated"])

            doc.write_text("PDC changed", encoding="utf-8")
            stale = self.run_cli("context", "PDC", "--root", str(root), "--state-dir", str(state_dir), "--format", "json")
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("index is stale", stale.stderr)

    def test_context_pack_markdown_and_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, title="Markdown", content="PDC markdown memory")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            output = Path(tmp) / "context.md"

            result = self.run_cli("context", "PDC", "--root", str(root), "--state-dir", str(state_dir), "--format", "markdown", "--output", output)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            text = output.read_text(encoding="utf-8")
            self.assertIn("# Context Pack", text)
            self.assertIn("PDC markdown memory", text)


class RetrievalEvaluationCommandTests(unittest.TestCase):
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

    def run_add(self, root, title, content, **kwargs):
        args = [
            "add",
            "--confirmed",
            "--root",
            str(root),
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
        accepted = finish_add_fixture(root, result)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        return re.search(r"memory_id: (.+)", result.stdout).group(1)

    def setup_store(self, tmp):
        root = Path(tmp) / "data"
        state_dir = Path(tmp) / "state"
        self.assertEqual(self.run_init(root).returncode, 0)
        self.assertEqual(self.run_db_init(root, state_dir).returncode, 0)
        return root, state_dir

    def test_evaluate_search_reports_metrics_and_zero_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, "代码最少原则", "使用尽可能少的代码实现相同功能。")
            self.run_add(root, "Work internal", "代码最少 internal", workspace="work", confidentiality="internal")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)
            cases = Path(tmp) / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "cases": [
                            {"query": "代码最少", "expected_ids": ["代码最少原则"], "workspace": "personal", "synonym": True},
                            {"query": "missing phrase", "expected_ids": [], "workspace": "personal"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = self.run_cli("evaluate-search", "--root", str(root), "--state-dir", str(state_dir), "--cases", cases, "--mode", "lexical", "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metrics = json.loads(result.stdout)
            self.assertEqual(metrics["mode"], "lexical")
            self.assertEqual(metrics["case_count"], 2)
            self.assertEqual(metrics["restricted_leak_rate"], 0)
            self.assertEqual(metrics["project_leak_rate"], 0)
            self.assertGreaterEqual(metrics["top5"], metrics["top1"])
            self.assertIn("p95_latency_ms", metrics)

    def test_search_hybrid_falls_back_to_lexical_without_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state_dir = self.setup_store(tmp)
            self.run_add(root, "Hybrid", "PDC hybrid fallback")
            self.assertEqual(self.run_index(root, state_dir).returncode, 0)

            result = self.run_cli("search", "PDC", "--root", str(root), "--state-dir", str(state_dir), "--mode", "hybrid", "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            rows = payload["results"]
            self.assertTrue(rows)
            self.assertEqual(payload["requested_mode"], "hybrid")
            self.assertEqual(payload["effective_mode"], "lexical")
            self.assertTrue(payload["warnings"])
            self.assertIn("falling back to lexical", result.stderr)

    def test_retrieval_eval_template_exists(self):
        template = REPO_ROOT / "templates" / "retrieval_eval.json"
        self.assertTrue(template.is_file())
        data = json.loads(template.read_text(encoding="utf-8"))
        self.assertIn("cases", data)


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
            "--confirmed",
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
            manifest = self.load_manifest(root)
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["schema_version"], 3)
            self.assertEqual(manifest["record_count"], 0)
            self.assertEqual(manifest["records"], [])
            self.assertEqual(manifest["source_root"], str(root.resolve()))
            self.assertEqual(manifest["content_hash"], hashlib.sha256(b"").hexdigest())
            self.assertIn("generated_at", manifest)
            self.assertIn("database_hash", manifest)
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
            self.assertEqual(manifest_row["status"], "candidate")
            self.assertEqual(manifest_row["workspace"], "personal")
            self.assertEqual(manifest_row["confidentiality"], "personal")
            self.assertEqual(manifest_row["relative_path"], row["relative_path"])
            self.assertEqual(manifest_row["sha256"], source_hash)

    def test_export_order_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            self.assertEqual(self.run_init(root).returncode, 0)
            self.assertEqual(self.run_add(root, "session", "会话", "global").returncode, 0)
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


class V070MigrationAndSafetyTests(unittest.TestCase):
    def make_v2_database(self, base):
        module = load_memory_module()
        root = base / "data"
        module.init_store(root)
        record = {
            "id": "principle-legacy-1",
            "type": "principle",
            "title": "Legacy principle",
            "created": "2026-06-22",
            "updated": "2026-06-22",
            "status": "active",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "user",
            "confidence": "confirmed",
            "content": "legacy migration probe",
            "tags": [],
        }
        path = root / "memory/principles/principle-legacy-1-probe.md"
        module.atomic_write_text(path, module.render_memory(record))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source = base / "legacy-v2.sqlite"
        conn = sqlite3.connect(source)
        try:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    schema TEXT NOT NULL DEFAULT 'memory/v1',
                    tier TEXT NOT NULL DEFAULT 'long',
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]',
                    aliases TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    confidentiality TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    source_id TEXT,
                    source_path TEXT,
                    source_sha256 TEXT,
                    context_id TEXT,
                    project TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    created TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX idx_memories_type ON memories(type);
                CREATE INDEX idx_memories_project ON memories(project);
                CREATE INDEX idx_memories_context ON memories(context_id);
                CREATE INDEX idx_memories_workspace_status ON memories(workspace, status);
                CREATE VIRTUAL TABLE memory_fts USING fts5(
                    id UNINDEXED, title, content, body, tags, aliases, tokenize='unicode61'
                );
                CREATE TABLE index_state (
                    relative_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL, indexed_at TEXT NOT NULL
                );
                CREATE TABLE links (
                    source_id TEXT NOT NULL, target_id TEXT, target_ref TEXT NOT NULL,
                    relation TEXT NOT NULL, source_kind TEXT NOT NULL,
                    target_heading TEXT, target_block TEXT, context TEXT,
                    resolved INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX idx_links_ref ON links(target_ref);
                CREATE INDEX idx_links_source ON links(source_id);
                CREATE INDEX idx_links_target ON links(target_id);
                CREATE TABLE distillation_audit (
                    recent_id TEXT PRIMARY KEY, relative_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}', distilled_at TEXT,
                    delete_after TEXT, deleted_at TEXT, error TEXT
                );
                PRAGMA user_version = 2;
                """
            )
            conn.execute(
                """INSERT INTO memories (
                    id,type,title,content,status,scope,workspace,confidentiality,
                    source,confidence,created,updated,relative_path,sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["id"], record["type"], record["title"], record["content"],
                    record["status"], record["scope"], record["workspace"],
                    record["confidentiality"], record["source"], record["confidence"],
                    record["created"], record["updated"], path.relative_to(root).as_posix(), digest,
                ),
            )
            conn.execute(
                "INSERT INTO memory_fts(id,title,content,body,tags,aliases) VALUES (?,?,?,?,?,?)",
                (record["id"], record["title"], record["content"], "", "", ""),
            )
            conn.execute(
                "INSERT INTO index_state VALUES (?,?,?,?)",
                (path.relative_to(root).as_posix(), digest, path.stat().st_mtime_ns, "2026-06-22T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO links(source_id,target_ref,relation,source_kind,resolved) VALUES (?,?,?,?,?)",
                (record["id"], "legacy-ref", "related_to", "body", 0),
            )
            conn.commit()
        finally:
            conn.close()
        return module, root, source, digest

    def test_migrate_v2_preserves_legacy_schema_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            module, root, source, digest = self.make_v2_database(base)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            output = base / "candidate-v3.sqlite"

            summary = module.migrate_database_v2_to_v3(source, output, root)

            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertEqual(module.inspect_database(output), {"initialized": True, "version": 3})
            conn = sqlite3.connect(output)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
                self.assertTrue({"schema", "tier", "body", "aliases", "source_id", "source_path", "source_sha256", "metadata_json"}.issubset(columns))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT sha256 FROM memories").fetchone()[0], digest)
            finally:
                conn.close()
            self.assertEqual(summary["memories"], 1)

    def test_migrate_v2_is_repeatable_but_never_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            module, root, source, _ = self.make_v2_database(base)
            first = base / "first.sqlite"
            second = base / "second.sqlite"

            module.migrate_database_v2_to_v3(source, first, root)
            module.migrate_database_v2_to_v3(source, second, root)

            self.assertEqual(module.inspect_database(first), module.inspect_database(second))
            with self.assertRaisesRegex(ValueError, "must differ"):
                module.migrate_database_v2_to_v3(source, source, root)

    def test_migration_failure_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            module, root, source, _ = self.make_v2_database(base)
            conn = sqlite3.connect(source)
            try:
                conn.execute("DROP TABLE index_state")
                conn.commit()
            finally:
                conn.close()
            output = base / "broken-output.sqlite"

            with self.assertRaises(ValueError):
                module.migrate_database_v2_to_v3(source, output, root)

            self.assertFalse(output.exists())

    def test_inspect_accepts_extra_columns_but_rejects_missing_or_wrong_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            module = load_memory_module()
            compatible = base / "compatible.sqlite"
            module.create_database(compatible)
            conn = sqlite3.connect(compatible)
            try:
                conn.execute("ALTER TABLE memories ADD COLUMN legacy_note TEXT")
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(module.inspect_database(compatible)["initialized"])

            equivalent = base / "equivalent-index.sqlite"
            shutil.copy2(compatible, equivalent)
            conn = sqlite3.connect(equivalent)
            try:
                conn.execute("DROP INDEX idx_memories_type")
                conn.execute("CREATE INDEX legacy_custom_type_index ON memories(type)")
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(module.inspect_database(equivalent)["initialized"])

            missing = base / "missing.sqlite"
            shutil.copy2(compatible, missing)
            conn = sqlite3.connect(missing)
            try:
                conn.execute("ALTER TABLE memories DROP COLUMN source")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(ValueError, "columns"):
                module.inspect_database(missing)

            wrong = base / "wrong.sqlite"
            shutil.copy2(compatible, wrong)
            conn = sqlite3.connect(wrong)
            try:
                conn.execute("PRAGMA writable_schema = ON")
                conn.execute("UPDATE sqlite_master SET sql=replace(sql, 'source TEXT NOT NULL', 'source BLOB NOT NULL') WHERE name='memories'")
                conn.execute("PRAGMA writable_schema = OFF")
                version = conn.execute("PRAGMA schema_version").fetchone()[0]
                conn.execute(f"PRAGMA schema_version = {version + 1}")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(ValueError, "column types"):
                module.inspect_database(wrong)

            broken_fts = base / "broken-fts.sqlite"
            shutil.copy2(compatible, broken_fts)
            conn = sqlite3.connect(broken_fts)
            try:
                conn.execute("DROP TABLE memory_fts")
                conn.execute("CREATE TABLE memory_fts(id TEXT, title TEXT, content TEXT, body TEXT, tags TEXT, aliases TEXT)")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaisesRegex(ValueError, "FTS5"):
                module.inspect_database(broken_fts)

    def test_migrated_legacy_recent_is_preserved_by_index_and_hidden_from_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            module, root, source, digest = self.make_v2_database(base)
            old_path = next((root / "memory/principles").glob("*.md"))
            recent_dir = root / "memory/recent"
            recent_dir.mkdir(parents=True)
            recent_path = recent_dir / old_path.name
            old_path.replace(recent_path)
            relative = recent_path.relative_to(root).as_posix()
            conn = sqlite3.connect(source)
            try:
                conn.execute(
                    "UPDATE memories SET tier='recent', relative_path=? WHERE id='principle-legacy-1'",
                    (relative,),
                )
                conn.execute("DELETE FROM index_state")
                conn.execute(
                    "INSERT INTO index_state VALUES (?,?,?,?)",
                    (relative, digest, recent_path.stat().st_mtime_ns, "2026-06-22T00:00:00+00:00"),
                )
                conn.commit()
            finally:
                conn.close()
            state = base / "state"
            state.mkdir()
            output = state / "memory.sqlite"
            module.migrate_database_v2_to_v3(source, output, root)

            result = module.index_store(argparse.Namespace(root=str(root), state_dir=str(state), dry_run=False))

            self.assertEqual(result["deleted"], 0)
            conn = sqlite3.connect(output)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0], 1)
            finally:
                conn.close()
            rows = module.search_store(argparse.Namespace(
                root=str(root), state_dir=str(state), query="Legacy migration probe",
                kind="memory", source_kind=None, type=None, project=None, context_id=None,
                workspace=None, status=None, as_of=None, include_unassigned=True,
                include_restricted=False, include_inactive=False, limit=20,
            ))
            self.assertEqual(rows, [])

    def test_replace_transaction_restores_all_existing_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = load_memory_module()
            base = Path(tmp)
            first = base / "first.md"
            second = base / "second.md"
            first.write_text("old first", encoding="utf-8")
            second.write_text("old second", encoding="utf-8")
            original_replace = module.os.replace
            replacements = 0
            failed = False

            def fail_second(source, target):
                nonlocal replacements, failed
                if Path(target) in {first, second} and Path(source).name.startswith(".tmp-"):
                    replacements += 1
                    if replacements == 2 and not failed:
                        failed = True
                        raise OSError("injected failure")
                return original_replace(source, target)

            with mock.patch.object(module.os, "replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "injected failure"):
                    module._replace_transaction([(first, "new first"), (second, "new second")], [])

            self.assertEqual(first.read_text(encoding="utf-8"), "old first")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second")
            self.assertFalse([p for p in base.iterdir() if p.name.startswith((".tmp-", ".backup-"))])


if __name__ == "__main__":
    unittest.main()
