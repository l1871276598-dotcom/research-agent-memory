import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
AGENT_CLI = SOURCE_DIR / "memory_agent.py"
MEMORY_CLI = SOURCE_DIR / "memory.py"
DISTILL_CLI = SOURCE_DIR / "memory_distill.py"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory
import memory_agent


def recalled_item(item_id, title, excerpt, relative_path=None):
    return {
        "id": item_id,
        "title": title,
        "kind": "memory",
        "source_kind": "memory",
        "relative_path": relative_path or f"memory/principles/{item_id}.md",
        "excerpt": excerpt,
        "project": None,
    }


class MemoryAgentTests(unittest.TestCase):
    def run_cli(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, str(AGENT_CLI), *map(str, args)],
            input=input_text,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_memory(self, *args):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), *map(str, args)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def run_distill(self, *args):
        return subprocess.run(
            [sys.executable, str(DISTILL_CLI), *map(str, args)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def init_root(self, tmp, indexed=False):
        root = Path(tmp) / "data"
        state = Path(tmp) / "state"
        result = self.run_memory("init", "--root", root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if indexed:
            result = self.run_memory("db-init", "--root", root, "--state-dir", state)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = self.run_memory("index", "--root", root, "--state-dir", state)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root, state

    def active_memory(self, root, state):
        result = self.run_memory(
            "add",
            "--root",
            root,
            "--type",
            "principle",
            "--title",
            "Authoritative",
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
            "Do not overwrite this memory.",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        memory_id = re.search(r"memory_id: (.+)", result.stdout).group(1)
        accepted = self.run_distill(
            "accept", "--root", root, "--state-dir", state, "--id", memory_id
        )
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        return next(path for path in (root / "memory").rglob("*.md") if memory_id in path.name)

    def parse_stdout(self, result):
        return json.loads(result.stdout)

    def test_help_commands_return_zero(self):
        for args in (("--help",), ("prepare", "--help"), ("finalize", "--help")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_helper_uses_current_python(self):
        with mock.patch("subprocess.run") as run:
            self.run_cli("--help")
        self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_unknown_subcommand_returns_stable_json_error(self):
        result = self.run_cli("unknown")
        self.assertNotEqual(result.returncode, 0)
        payload = self.parse_stdout(result)
        self.assertEqual(payload["error"]["code"], "invalid_subcommand")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_required_argument_returns_stable_json_error(self):
        result = self.run_cli("prepare", "--root", "missing")
        self.assertNotEqual(result.returncode, 0)
        payload = self.parse_stdout(result)
        self.assertEqual(payload["operation"], "prepare")
        self.assertEqual(payload["error"]["code"], "invalid_arguments")
        self.assertNotIn("Traceback", result.stderr)

    def test_prepare_empty_index_is_valid_json_and_handles_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp, indexed=True)
            result = self.run_cli(
                "prepare",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "整理中文研究任务",
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self.parse_stdout(result)
        self.assertEqual(payload["operation"], "prepare")
        self.assertEqual(payload["context"], "")
        self.assertEqual(payload["context_chars"], 0)
        self.assertEqual(payload["max_chars"], 8000)
        self.assertEqual(payload["sources"], [])
        self.assertIsInstance(payload["warnings"], list)

    def test_prepare_preserves_order_deduplicates_and_reports_real_sources(self):
        rows = [
            recalled_item("b", "第二条", "第二段"),
            recalled_item("a", "第一条", "第一段"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            with mock.patch.object(
                memory_agent.memory_tools, "search_store", return_value=[rows[0], rows[0], rows[1]]
            ):
                first = memory_agent.prepare_memory(root, "中文任务", state_dir=state)
                second = memory_agent.prepare_memory(root, "中文任务", state_dir=state)

        self.assertEqual(first, second)
        self.assertLess(first["context"].index("第二条"), first["context"].index("第一条"))
        self.assertEqual(first["context"].count("第二条"), 1)
        self.assertEqual([source["id"] for source in first["sources"]], ["b", "a"])
        self.assertTrue(all(source["id"] in {row["id"] for row in rows} for source in first["sources"]))

    def test_prepare_strictly_limits_small_unicode_budget(self):
        row = recalled_item("cn", "中文标题", "汉字内容不会按字节切坏")
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            with mock.patch.object(memory_agent.memory_tools, "search_store", return_value=[row]):
                payload = memory_agent.prepare_memory(
                    root, "中文任务", state_dir=state, max_chars=7
                )

        self.assertEqual(payload["context_chars"], len(payload["context"]))
        self.assertLessEqual(payload["context_chars"], 7)
        payload["context"].encode("utf-8")

    def test_prepare_rejects_zero_and_negative_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            for value in (0, -1):
                with self.subTest(value=value):
                    result = self.run_cli(
                        "prepare",
                        "--root",
                        root,
                        "--state-dir",
                        state,
                        "--task",
                        "task",
                        "--max-chars",
                        str(value),
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        self.parse_stdout(result)["error"]["code"], "invalid_max_chars"
                    )
                    self.assertNotIn("Traceback", result.stderr)

    def test_prepare_passes_project_and_existing_search_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            with mock.patch.object(
                memory_agent.memory_tools, "search_store", return_value=[]
            ) as search:
                memory_agent.prepare_memory(root, "task", state_dir=state, project="pdc")

        args = search.call_args.args[0]
        self.assertEqual(args.project, "pdc")
        self.assertEqual(args.query, "task")
        self.assertEqual(args.kind, "all")
        self.assertFalse(args.include_restricted)
        self.assertFalse(args.include_inactive)

    def test_prepare_does_not_modify_memories_or_create_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with mock.patch.object(memory_agent.memory_tools, "search_store", return_value=[]):
                memory_agent.prepare_memory(root, "task", state_dir=state)
            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

        self.assertEqual(after, before)
        self.assertFalse(any("candidate" in data.decode("utf-8", errors="ignore") for data in after.values()))

    def test_prepare_maps_invalid_root_state_and_search_errors(self):
        missing = Path(tempfile.gettempdir()) / "memory-agent-missing-root"
        with self.assertRaises(memory_agent.AgentError) as raised:
            memory_agent.prepare_memory(missing, "task", state_dir=missing.parent / "state")
        self.assertEqual(raised.exception.code, "invalid_memory_root")

        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.init_root(tmp)
            with self.assertRaises(memory_agent.AgentError) as raised:
                memory_agent.prepare_memory(root, "task", state_dir=root / "state")
            self.assertEqual(raised.exception.code, "invalid_state_dir")

            with mock.patch.object(
                memory_agent.memory_tools, "search_store", side_effect=ValueError("sensitive path")
            ):
                with self.assertRaises(memory_agent.AgentError) as raised:
                    memory_agent.prepare_memory(root, "task", state_dir=Path(tmp) / "state")
            self.assertEqual(raised.exception.code, "search_failed")
            self.assertNotIn("sensitive path", str(raised.exception))

    def test_default_state_dir_is_delegated_for_each_platform(self):
        for platform in ("windows", "macos", "linux"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "data"
                state = Path(tmp) / platform
                with (
                    mock.patch.object(memory_agent.memory, "_require_data_root", return_value=root),
                    mock.patch.object(
                        memory_agent.platform_paths, "default_state_dir", return_value=state
                    ) as default_state,
                    mock.patch.object(
                        memory_agent.memory, "check_state_dir", return_value=(state, state / "memory.sqlite")
                    ) as check_state,
                    mock.patch.object(memory_agent.memory_tools, "search_store", return_value=[]),
                ):
                    memory_agent.prepare_memory(root, "task")
                default_state.assert_called_once_with()
                check_state.assert_called_once_with(root, state)

    def test_finalize_creates_one_real_review_candidate_with_chinese_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            payload = memory_agent.finalize_memory(
                root, "完成中文任务", "已完成，保留证据。", state_dir=state
            )

            self.assertEqual(payload["candidate_count"], 1)
            self.assertTrue(payload["review_required"])
            self.assertFalse(payload["applied"])
            self.assertEqual(len(payload["artifacts"]), 1)
            artifact = payload["artifacts"][0]
            candidate = root / artifact["path"]
            self.assertTrue(candidate.is_file())
            text = candidate.read_text(encoding="utf-8")
            self.assertIn("完成中文任务", text)
            self.assertIn("已完成，保留证据。", text)
            self.assertIn('status: "candidate"', text)
            self.assertIn('audit_status: "awaiting_review"', text)
            self.assertNotIn('status: "active"', text)

    def test_finalize_blank_result_is_valid_no_candidate_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            payload = memory_agent.finalize_memory(root, "task", "   ", state_dir=state)

        self.assertEqual(payload["candidate_count"], 0)
        self.assertEqual(payload["artifacts"], [])
        self.assertTrue(payload["review_required"])
        self.assertFalse(payload["applied"])

    def test_finalize_duplicate_result_reports_real_zero_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            first = memory_agent.finalize_memory(root, "task", "result", state_dir=state)
            second = memory_agent.finalize_memory(root, "task", "result", state_dir=state)

        self.assertEqual(first["candidate_count"], 1)
        self.assertEqual(second["candidate_count"], 0)
        self.assertEqual(second["artifacts"], [])

    def test_finalize_result_file_supports_long_chinese_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result_file = Path(tmp) / "result.txt"
            result_file.write_text("中文结果" * 5000, encoding="utf-8")
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "原始任务",
                "--result-file",
                result_file,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = self.parse_stdout(result)
        self.assertEqual(payload["operation"], "finalize")
        self.assertEqual(payload["candidate_count"], 1)

    def test_finalize_conflicting_or_missing_result_input_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result_file = Path(tmp) / "result.txt"
            result_file.write_text("file", encoding="utf-8")
            conflicting = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "task",
                "--result",
                "text",
                "--result-file",
                result_file,
            )
            missing = self.run_cli(
                "finalize", "--root", root, "--state-dir", state, "--task", "task"
            )

        self.assertNotEqual(conflicting.returncode, 0)
        self.assertEqual(
            self.parse_stdout(conflicting)["error"]["code"], "conflicting_input"
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.parse_stdout(missing)["error"]["code"], "missing_input")
        self.assertNotIn("Traceback", conflicting.stderr + missing.stderr)

    def test_finalize_does_not_modify_authoritative_memory_or_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp, indexed=True)
            authoritative = self.active_memory(root, state)
            before = authoritative.read_bytes()

            payload = memory_agent.finalize_memory(root, "task", "new result", state_dir=state)

            self.assertEqual(authoritative.read_bytes(), before)
            self.assertFalse(payload["applied"])
            self.assertTrue(payload["review_required"])

    def test_finalize_maps_distillation_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            output = io.StringIO()
            errors = io.StringIO()
            with (
                mock.patch.object(
                    memory_agent.memory_distill,
                    "apply_candidate",
                    side_effect=memory_agent.memory_distill.DistillError(
                        "apply_failed", "sensitive failure"
                    ),
                ),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                code = memory_agent.main(
                    [
                        "finalize",
                        "--root",
                        str(root),
                        "--state-dir",
                        str(state),
                        "--task",
                        "task",
                        "--result",
                        "result",
                    ]
                )

        self.assertNotEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "distillation_failed")
        self.assertNotIn("sensitive failure", payload["error"]["message"])
        self.assertNotIn("Traceback", errors.getvalue())

    def test_finalize_rejects_unreadable_result_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "task",
                "--result-file",
                Path(tmp) / "missing.txt",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.parse_stdout(result)["error"]["code"], "missing_input")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
