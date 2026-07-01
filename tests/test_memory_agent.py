import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
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
        for args in (
            ("--help",),
            ("prepare", "--help"),
            ("finalize", "--help"),
            ("approve-policies", "--help"),
        ):
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
            self.assertNotIn("loop_run", payload)
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
        self.assertNotIn("loop_run", payload)

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

    def test_finalize_rejects_oversized_task_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "12345",
                "--result",
                "ok",
                "--max-task-chars",
                "4",
            )

            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.parse_stdout(result)["error"]["code"], "task_too_large")
        self.assertEqual(after, before)
        self.assertFalse(state.exists())

    def test_finalize_rejects_oversized_result_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "task",
                "--result",
                "12345",
                "--max-result-chars",
                "4",
                "--outcome",
                "fail",
            )

            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.parse_stdout(result)["error"]["code"], "result_too_large")
        self.assertEqual(after, before)
        self.assertFalse(state.exists())

    def test_finalize_rejects_oversized_candidate_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "task",
                "--result",
                "result",
                "--max-candidate-chars",
                "20",
                "--outcome",
                "pass",
            )

            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.parse_stdout(result)["error"]["code"], "candidate_too_large")
        self.assertEqual(after, before)
        self.assertFalse(state.exists())

    def test_finalize_rejects_invalid_maximums_with_stable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            for option, value in (
                ("--max-task-chars", "0"),
                ("--max-result-chars", "-1"),
                ("--max-candidate-chars", "not-an-integer"),
            ):
                with self.subTest(option=option, value=value):
                    result = self.run_cli(
                        "finalize",
                        "--root",
                        root,
                        "--state-dir",
                        state,
                        "--task",
                        "task",
                        "--result",
                        "result",
                        option,
                        value,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        self.parse_stdout(result)["error"]["code"], "invalid_max_chars"
                    )
                    self.assertNotIn("Traceback", result.stderr)

    def test_finalize_result_file_uses_same_result_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result_file = Path(tmp) / "result.txt"
            result_file.write_text("中文结果", encoding="utf-8")
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "task",
                "--result-file",
                result_file,
                "--max-result-chars",
                "3",
                "--outcome",
                "pass",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.parse_stdout(result)["error"]["code"], "result_too_large")
        self.assertFalse(state.exists())

    def test_finalize_pass_creates_complete_loop_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "验证迁移",
                "--result",
                "迁移验证通过",
                "--outcome",
                "pass",
                "--reflection",
                "预检查成功阻止了错误目标。",
                "--policy-suggestion",
                "迁移前验证目标状态。",
            )

            payload = self.parse_stdout(result)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run_dir = state / payload["loop_run"]["path"]
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            reflection = (run_dir / "reflection.md").read_text(encoding="utf-8")
            policies = (run_dir / "policy_suggestions.md").read_text(encoding="utf-8")
            file_names = {path.name for path in run_dir.iterdir()}
            candidate_exists = (root / payload["artifacts"][0]["path"]).is_file()

        self.assertEqual(
            file_names,
            {"run.json", "reflection.md", "policy_suggestions.md"},
        )
        self.assertEqual(run_data["schema_version"], 1)
        self.assertEqual(run_data["run_id"], payload["loop_run"]["run_id"])
        self.assertEqual(
            run_data["task_sha256"], hashlib.sha256("验证迁移".encode("utf-8")).hexdigest()
        )
        self.assertEqual(
            run_data["result_sha256"],
            hashlib.sha256("迁移验证通过".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(run_data["outcome"], "pass")
        self.assertEqual(len(payload["artifacts"]), 1)
        artifact = payload["artifacts"][0]
        self.assertEqual(run_data["candidate_ids"], [artifact["id"]])
        self.assertTrue(candidate_exists)
        self.assertTrue(run_data["review_required"])
        self.assertEqual(run_data["approved_policy_count"], 0)
        self.assertEqual(run_data["status"], "waiting_for_human")
        self.assertIn("## Task\n\n验证迁移", reflection)
        self.assertIn("## Outcome\n\npass", reflection)
        self.assertIn("## Result Evidence\n\n迁移验证通过", reflection)
        self.assertIn("## Error Evidence\n\nNone", reflection)
        self.assertIn("## What Worked\n\n预检查成功阻止了错误目标。", reflection)
        self.assertRegex(policies, r"- \[ \] 迁移前验证目标状态。 <!-- policy_id: [0-9a-f]{16} -->")

    def test_finalize_blank_result_with_outcome_keeps_candidate_ids_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            payload = memory_agent.finalize_memory(
                root, "task", "   ", state_dir=state, outcome="pass"
            )
            run_dir = state / payload["loop_run"]["path"]
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["candidate_count"], 0)
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(run_data["candidate_ids"], [])

    def test_finalize_fail_records_error_and_reflection_without_inventing_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            result = self.run_cli(
                "finalize",
                "--root",
                root,
                "--state-dir",
                state,
                "--task",
                "执行迁移",
                "--result",
                "迁移失败",
                "--outcome",
                "fail",
                "--error",
                "目标版本不匹配",
                "--reflection",
                "版本预检查缺失。",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = self.parse_stdout(result)
            run_dir = state / payload["loop_run"]["path"]
            reflection = (run_dir / "reflection.md").read_text(encoding="utf-8")

        self.assertIn("## Outcome\n\nfail", reflection)
        self.assertIn("## Error Evidence\n\n目标版本不匹配", reflection)
        self.assertIn("## What Failed\n\n版本预检查缺失。", reflection)
        self.assertIn("## Root Cause\n\nPending human or agent review.", reflection)
        self.assertIn("## Next Change\n\nPending human or agent review.", reflection)

    def test_finalize_without_policy_suggestion_does_not_invent_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            payload = memory_agent.finalize_memory(
                root, "task", "result", state_dir=state, outcome="pass"
            )
            self.assertIn("loop_run", payload)
            run_dir = state / payload["loop_run"]["path"]
            policies = (run_dir / "policy_suggestions.md").read_text(encoding="utf-8")

        self.assertEqual(
            policies,
            "# Policy Suggestions\n\nNo policy suggestion was supplied. Human review is required.\n",
        )

    def test_finalize_writes_multiple_policy_suggestions_with_stable_ids(self):
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
                "--result",
                "result",
                "--outcome",
                "fail",
                "--policy-suggestion",
                "先检查状态。",
                "--policy-suggestion",
                "保留失败证据。",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = self.parse_stdout(result)
            run_dir = state / payload["loop_run"]["path"]
            policies = (run_dir / "policy_suggestions.md").read_text(encoding="utf-8")

        self.assertEqual(policies.count("- [ ]"), 2)
        self.assertEqual(len(re.findall(r"policy_id: [0-9a-f]{16}", policies)), 2)
        self.assertIn("先检查状态。", policies)
        self.assertIn("保留失败证据。", policies)

    def test_finalize_loop_write_failure_leaves_no_run_or_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            real_write = memory_agent.memory.atomic_write_text

            def fail_policy(path, content):
                if path.name == "policy_suggestions.md":
                    raise OSError("sensitive write failure")
                return real_write(path, content)

            with mock.patch.object(
                memory_agent.memory, "atomic_write_text", side_effect=fail_policy
            ):
                with self.assertRaises(memory_agent.AgentError) as raised:
                    memory_agent.finalize_memory(
                        root, "task", "result", state_dir=state, outcome="pass"
                    )

            run_root = state / "loop_engineering" / "runs"
            candidates = list((root / "memory" / "sessions").glob("*.md"))

        self.assertEqual(raised.exception.code, "loop_write_failed")
        self.assertEqual(list(run_root.iterdir()) if run_root.exists() else [], [])
        self.assertEqual(candidates, [])

    def test_finalize_staging_failure_returns_stable_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            output = io.StringIO()
            errors = io.StringIO()
            caught = None
            with (
                mock.patch.object(
                    memory_agent.tempfile,
                    "mkdtemp",
                    side_effect=OSError("sensitive staging path"),
                ),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                try:
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
                            "--outcome",
                            "pass",
                        ]
                    )
                except Exception as exc:  # pragma: no cover - assertion documents regression
                    caught = exc

        self.assertIsNone(caught)
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "loop_write_failed")
        self.assertNotIn("sensitive staging path", output.getvalue() + errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    @unittest.skipIf(os.name == "nt", "symlink creation is not reliably available on Windows")
    def test_finalize_rejects_symlinked_loop_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            outside = Path(tmp) / "outside"
            outside.mkdir()
            state.mkdir()
            (state / "loop_engineering").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(memory_agent.AgentError) as raised:
                memory_agent.finalize_memory(
                    root, "task", "result", state_dir=state, outcome="pass"
                )

            candidates = list((root / "memory" / "sessions").glob("*.md"))
            outside_files = list(outside.rglob("*"))

        self.assertEqual(raised.exception.code, "invalid_state_dir")
        self.assertEqual(candidates, [])
        self.assertEqual(outside_files, [])

    def test_finalize_candidate_failure_removes_published_loop_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)

            published_before_candidate = {"value": False}

            def fail_candidate(args):
                run_root = state / "loop_engineering" / "runs"
                published_before_candidate["value"] = any(
                    path.is_dir() and not path.name.startswith(".tmp-")
                    for path in run_root.iterdir()
                )
                raise memory_agent.memory_distill.DistillError(
                    "apply_failed", "sensitive failure"
                )

            with mock.patch.object(
                memory_agent.memory_distill,
                "apply_candidate",
                side_effect=fail_candidate,
            ):
                with self.assertRaises(memory_agent.AgentError) as raised:
                    memory_agent.finalize_memory(
                        root,
                        "task",
                        "result",
                        state_dir=state,
                        outcome="fail",
                    )

            run_root = state / "loop_engineering" / "runs"

        self.assertEqual(raised.exception.code, "distillation_failed")
        self.assertTrue(published_before_candidate["value"])
        self.assertEqual(list(run_root.iterdir()) if run_root.exists() else [], [])

    def test_finalize_link_failure_rolls_back_run_candidate_journal_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp, indexed=True)
            journal = root / "state" / "distill_runs.jsonl"
            journal_before = journal.read_bytes() if journal.exists() else None
            captured = {}
            real_apply = memory_agent.memory_distill.apply_candidate
            real_write = memory_agent.memory.atomic_write_text

            def apply_and_index(args):
                summary = real_apply(args)
                captured.update(summary)
                memory_agent.memory.index_store(
                    memory_agent.argparse.Namespace(
                        root=str(root), state_dir=str(state), dry_run=False
                    )
                )
                return summary

            def fail_link_write(path, content):
                if (
                    path.name == "run.json"
                    and path.parent.parent.name == "runs"
                    and re.fullmatch(r"[0-9a-f]{32}", path.parent.name)
                ):
                    raise OSError("sensitive linkage failure")
                return real_write(path, content)

            output = io.StringIO()
            errors = io.StringIO()
            with (
                mock.patch.object(
                    memory_agent.memory_distill,
                    "apply_candidate",
                    side_effect=apply_and_index,
                ),
                mock.patch.object(
                    memory_agent.memory,
                    "atomic_write_text",
                    side_effect=fail_link_write,
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
                        "--outcome",
                        "fail",
                    ]
                )

            run_root = state / "loop_engineering" / "runs"
            run_entries = list(run_root.iterdir()) if run_root.exists() else []
            candidate_exists = bool(captured.get("path")) and (
                root / captured["path"]
            ).exists()
            journal_after = journal.read_bytes() if journal.exists() else None
            with contextlib.closing(sqlite3.connect(state / "memory.sqlite")) as conn:
                indexed = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE id=?",
                    (captured.get("candidate_id"),),
                ).fetchone()[0]

        self.assertNotEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "loop_link_failed")
        self.assertNotIn("sensitive linkage failure", output.getvalue() + errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        self.assertEqual(run_entries, [])
        self.assertFalse(candidate_exists)
        self.assertEqual(indexed, 0)
        self.assertEqual(journal_after, journal_before)

    def test_approve_policies_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="pass",
                policy_suggestions=["先验证目标状态。"],
            )
            run_id = finalized["loop_run"]["run_id"]
            run_json = state / finalized["loop_run"]["path"] / "run.json"
            before = run_json.read_bytes()
            result = self.run_cli(
                "approve-policies",
                "--root",
                root,
                "--state-dir",
                state,
                "--run-id",
                run_id,
            )
            after = run_json.read_bytes()
            rules_exists = (root / "memory_rules.md").exists()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.parse_stdout(result)["error"]["code"], "confirmation_required"
        )
        self.assertEqual(after, before)
        self.assertFalse(rules_exists)

    def test_approve_policies_does_not_apply_unchecked_suggestions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="pass",
                policy_suggestions=["先验证目标状态。"],
            )
            run_dir = state / finalized["loop_run"]["path"]
            result = self.run_cli(
                "approve-policies",
                "--root",
                root,
                "--state-dir",
                state,
                "--run-id",
                finalized["loop_run"]["run_id"],
                "--confirmed",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            rules_exists = (root / "memory_rules.md").exists()

        self.assertFalse(rules_exists)
        self.assertEqual(run_data["status"], "completed")
        self.assertEqual(run_data["approved_policy_count"], 0)

    def test_approve_policies_accepts_lower_and_upper_checked_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "task",
                "failed result",
                state_dir=state,
                outcome="fail",
                policy_suggestions=["先验证目标状态。", "保留失败证据。"],
            )
            run_dir = state / finalized["loop_run"]["path"]
            policy_path = run_dir / "policy_suggestions.md"
            policies = policy_path.read_text(encoding="utf-8")
            policies = policies.replace("- [ ]", "- [x]", 1).replace("- [ ]", "- [X]", 1)
            policy_path.write_text(policies, encoding="utf-8")
            result = self.run_cli(
                "approve-policies",
                "--root",
                root,
                "--state-dir",
                state,
                "--run-id",
                finalized["loop_run"]["run_id"],
                "--confirmed",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rules = (root / "memory_rules.md").read_text(encoding="utf-8")
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

        self.assertIn("# Memory Rules", rules)
        self.assertEqual(rules.count("## rule-"), 2)
        self.assertIn("- Policy: 先验证目标状态。", rules)
        self.assertIn("- Policy: 保留失败证据。", rules)
        self.assertIn(f"- Source run: {finalized['loop_run']['run_id']}", rules)
        self.assertIn("- Outcome: fail", rules)
        self.assertRegex(rules, r"- Approved: \d{4}-\d{2}-\d{2}T")
        self.assertEqual(run_data["status"], "completed")
        self.assertEqual(run_data["approved_policy_count"], 2)

    def test_approve_policies_deduplicates_content_and_preserves_manual_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            rules_path = root / "memory_rules.md"
            rules_path.write_text("# Memory Rules\n\nManual review note.\n", encoding="utf-8")
            for number in range(2):
                finalized = memory_agent.finalize_memory(
                    root,
                    f"task {number}",
                    f"result {number}",
                    state_dir=state,
                    outcome="pass",
                    policy_suggestions=["先验证目标状态。"],
                )
                run_dir = state / finalized["loop_run"]["path"]
                policy_path = run_dir / "policy_suggestions.md"
                policy_path.write_text(
                    policy_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                    encoding="utf-8",
                )
                result = self.run_cli(
                    "approve-policies",
                    "--root",
                    root,
                    "--state-dir",
                    state,
                    "--run-id",
                    finalized["loop_run"]["run_id"],
                    "--confirmed",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rules = rules_path.read_text(encoding="utf-8")

        self.assertIn("Manual review note.", rules)
        self.assertEqual(rules.count("## rule-"), 1)
        self.assertEqual(rules.count("- Policy: 先验证目标状态。"), 1)

    def test_approve_policies_rejects_invalid_run_id_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            for run_id in ("not-a-run", "../escape", "a" * 31, "g" * 32):
                with self.subTest(run_id=run_id):
                    result = self.run_cli(
                        "approve-policies",
                        "--root",
                        root,
                        "--state-dir",
                        state,
                        "--run-id",
                        run_id,
                        "--confirmed",
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        self.parse_stdout(result)["error"]["code"], "invalid_run_id"
                    )
            rules_exists = (root / "memory_rules.md").exists()
        self.assertFalse(rules_exists)

    def test_approve_policies_transaction_failure_does_not_complete_run(self):
        self.assertTrue(hasattr(memory_agent, "approve_policies"))
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="fail",
                policy_suggestions=["保留失败证据。"],
            )
            run_dir = state / finalized["loop_run"]["path"]
            policy_path = run_dir / "policy_suggestions.md"
            policy_path.write_text(
                policy_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                encoding="utf-8",
            )
            with mock.patch.object(
                memory_agent.memory,
                "_replace_transaction",
                side_effect=OSError("sensitive transaction failure"),
            ):
                with self.assertRaises(memory_agent.AgentError) as raised:
                    memory_agent.approve_policies(
                        root, state_dir=state, run_id=finalized["loop_run"]["run_id"], confirmed=True
                    )
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            rules_exists = (root / "memory_rules.md").exists()

        self.assertEqual(raised.exception.code, "approval_failed")
        self.assertEqual(run_data["status"], "waiting_for_human")
        self.assertEqual(run_data["approved_policy_count"], 0)
        self.assertFalse(rules_exists)

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

    def test_finalize_loop_fields_without_outcome_fail_without_writes(self):
        for option, value in (
            ("--error", "sensitive error evidence"),
            ("--reflection", "sensitive reflection"),
            ("--policy-suggestion", "sensitive policy"),
        ):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                root, state = self.init_root(tmp)
                before = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                result = self.run_cli(
                    "finalize",
                    "--root",
                    root,
                    "--state-dir",
                    state,
                    "--task",
                    "task",
                    "--result",
                    "result",
                    option,
                    value,
                )
                after = {
                    path.relative_to(root): path.read_bytes()
                    for path in root.rglob("*")
                    if path.is_file()
                }

                self.assertNotEqual(result.returncode, 0)
                payload = self.parse_stdout(result)
                self.assertEqual(payload["error"]["code"], "conflicting_input")
                self.assertEqual(
                    payload["error"]["message"],
                    "Loop Engineering fields require an outcome.",
                )
                self.assertEqual(after, before)
                self.assertFalse(state.exists())
                self.assertNotIn(value, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_finalize_function_rejects_loop_fields_without_outcome_before_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            with self.assertRaises(memory_agent.AgentError) as raised:
                memory_agent.finalize_memory(
                    root,
                    "task",
                    "result",
                    state_dir=state,
                    reflection="sensitive reflection",
                )

            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

        self.assertEqual(raised.exception.code, "conflicting_input")
        self.assertEqual(
            raised.exception.message, "Loop Engineering fields require an outcome."
        )
        self.assertEqual(after, before)
        self.assertFalse(state.exists())

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
