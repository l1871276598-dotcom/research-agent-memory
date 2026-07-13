import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORYCTL = REPO_ROOT / "skills" / "research-memory" / "scripts" / "memoryctl.py"
SKILL = REPO_ROOT / "skills" / "research-memory" / "SKILL.md"


def load_memoryctl():
    spec = importlib.util.spec_from_file_location("research_memory_memoryctl", MEMORYCTL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemoryCtlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo with spaces"
        self.root = self.base / "root with spaces"
        self.state = self.base / "state with spaces"
        (self.repo / "src").mkdir(parents=True)
        self.root.mkdir()
        self.state.mkdir()
        (self.repo / "src" / "memory.py").write_text("# memory cli\n", encoding="utf-8")
        (self.repo / "src" / "memory_tools.py").write_text("# memory tools cli\n", encoding="utf-8")
        self.env = {
            "RESEARCH_MEMORY_REPO": str(self.repo),
            "RESEARCH_MEMORY_ROOT": str(self.root),
            "RESEARCH_MEMORY_STATE": str(self.state),
        }
        self.module = load_memoryctl()

    def tearDown(self):
        self.tmp.cleanup()

    def run_with_mock(self, argv, returncodes=(0,)):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            index = min(len(calls) - 1, len(returncodes) - 1)
            return subprocess.CompletedProcess(command, returncodes[index])

        with mock.patch.dict(os.environ, self.env, clear=False):
            with mock.patch.object(self.module.subprocess, "run", side_effect=fake_run):
                code = self.module.main(argv)
        return code, calls

    def test_path_with_spaces_is_passed_as_single_arguments(self):
        code, calls = self.run_with_mock(["search", "PDC"])

        self.assertEqual(code, 0)
        command, kwargs = calls[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], str(self.repo / "src" / "memory.py"))
        self.assertIn(str(self.root), command)
        self.assertIn(str(self.state), command)
        self.assertFalse(kwargs.get("shell", False))

    def test_defaults_do_not_embed_a_personal_home_path(self):
        self.assertEqual(self.module.DEFAULT_REPO, REPO_ROOT)
        self.assertNotIn("/Users/", MEMORYCTL.read_text(encoding="utf-8"))
        self.assertNotIn("/Users/", SKILL.read_text(encoding="utf-8"))

    def test_search_arguments_are_forwarded(self):
        code, calls = self.run_with_mock(["search", "PDC paper"])

        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0][0],
            [
                sys.executable,
                str(self.repo / "src" / "memory.py"),
                "search",
                "PDC paper",
                "--root",
                str(self.root),
                "--state-dir",
                str(self.state),
            ],
        )

    def test_context_arguments_are_forwarded(self):
        code, calls = self.run_with_mock(
            ["context", "PDC project", "--project", "pdc", "--workspace", "personal"]
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0][0],
            [
                sys.executable,
                str(self.repo / "src" / "memory.py"),
                "context",
                "PDC project",
                "--root",
                str(self.root),
                "--state-dir",
                str(self.state),
                "--format",
                "json",
                "--project",
                "pdc",
                "--workspace",
                "personal",
            ],
        )

    def test_recall_is_a_context_alias(self):
        code, calls = self.run_with_mock(["recall", "PDC project"])

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0][2:4], ["context", "PDC project"])
        self.assertIn("--format", calls[0][0])
        self.assertIn("json", calls[0][0])

    def test_doctor_and_project_status_use_v07_commands(self):
        doctor_code, doctor_calls = self.run_with_mock(["doctor"])
        project_code, project_calls = self.run_with_mock(["project-status", "pdc"])

        self.assertEqual(doctor_code, 0)
        self.assertEqual(project_code, 0)
        self.assertEqual(doctor_calls[0][0][2], "doctor")
        self.assertEqual(project_calls[0][0][2:4], ["project-status", "--root"])
        self.assertIn("pdc", project_calls[0][0])
        self.assertIn("--json", doctor_calls[0][0])
        self.assertIn("--json", project_calls[0][0])

    def test_validate_arguments_are_forwarded(self):
        code, calls = self.run_with_mock(["validate"])

        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0][0],
            [
                sys.executable,
                str(self.repo / "src" / "memory.py"),
                "validate",
                "--root",
                str(self.root),
            ],
        )

    def test_index_arguments_are_forwarded(self):
        code, calls = self.run_with_mock(["index"])

        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0][0],
            [
                sys.executable,
                str(self.repo / "src" / "memory.py"),
                "index",
                "--root",
                str(self.root),
                "--state-dir",
                str(self.state),
            ],
        )

    def test_import_file_runs_dry_run_before_import(self):
        source = self.base / "draft.docx"
        source.write_text("sample", encoding="utf-8")

        code, calls = self.run_with_mock(["import-file", str(source)])

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        dry_run = calls[0][0]
        real_run = calls[1][0]
        self.assertIn("--dry-run", dry_run)
        self.assertNotIn("--dry-run", real_run)
        self.assertEqual(dry_run[:4], real_run[:4])
        path_index = dry_run.index("--path")
        self.assertEqual(dry_run[path_index:path_index + 2], ["--path", str(source)])
        self.assertNotIn("--state-dir", dry_run)

    def test_import_file_stops_when_dry_run_fails(self):
        source = self.base / "draft.txt"
        source.write_text("sample", encoding="utf-8")

        code, calls = self.run_with_mock(["import-file", str(source)], returncodes=(7,))

        self.assertEqual(code, 7)
        self.assertEqual(len(calls), 1)
        self.assertIn("--dry-run", calls[0][0])

    def test_add_arguments_are_safely_forwarded(self):
        extra = [
            "--type",
            "principle",
            "--title",
            "quoted value; rm -rf /",
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
            "keep as one argument",
        ]

        code, calls = self.run_with_mock(["add", *extra])

        self.assertEqual(code, 0)
        self.assertEqual(
            calls[0][0],
            [
                sys.executable,
                str(self.repo / "src" / "memory.py"),
                "add",
                "--root",
                str(self.root),
                *extra,
            ],
        )
        self.assertFalse(calls[0][1].get("shell", False))

    def test_nonzero_subprocess_exit_code_is_returned(self):
        code, _ = self.run_with_mock(["validate"], returncodes=(12,))

        self.assertEqual(code, 12)

    def test_subprocess_run_never_uses_shell(self):
        code, calls = self.run_with_mock(["doctor"])

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0][2], "doctor")
        self.assertFalse(calls[0][1].get("shell", False))

    def test_missing_paths_report_errors(self):
        self.root.rmdir()

        with mock.patch.dict(os.environ, self.env, clear=False):
            stderr = StringIO()
            with redirect_stderr(stderr):
                code = self.module.main(["search", "PDC"])

        self.assertNotEqual(code, 0)
        self.assertIn("RESEARCH_MEMORY_ROOT", stderr.getvalue())
        self.assertIn(str(self.root), stderr.getvalue())

    def test_no_purge_or_delete_commands_exist(self):
        parser = self.module.build_parser()

        help_text = parser.format_help()
        self.assertNotIn("purge", help_text)
        self.assertNotIn("delete", help_text)
        self.assertNotIn("import-inbox", help_text)
        self.assertNotIn("outgoing", help_text)
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["purge"])

    def test_skill_documents_only_supported_v07_commands(self):
        text = SKILL.read_text(encoding="utf-8")

        for command in ("context", "doctor", "project-status"):
            self.assertIn(f"memoryctl.py {command}", text)
        for removed in ("import-inbox", "outgoing", "backlinks", "related", "unresolved", "check-links"):
            self.assertNotIn(f"memoryctl.py {removed}", text)


if __name__ == "__main__":
    unittest.main()
