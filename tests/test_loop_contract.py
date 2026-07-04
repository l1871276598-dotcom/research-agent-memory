import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
MEMORY_CLI = SOURCE_DIR / "memory.py"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory_agent


class LoopContractTests(unittest.TestCase):
    def init_root(self, tmp):
        root = Path(tmp) / "data"
        state = Path(tmp) / "state"
        result = subprocess.run(
            [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root, state

    def test_repeated_finalize_reuses_run_without_repeating_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            kwargs = {
                "state_dir": state,
                "outcome": "fail",
                "error": "target mismatch",
                "reflection": "preflight missing",
                "policy_suggestions": ["validate target first"],
            }

            first = memory_agent.finalize_memory(root, "migrate", "failed", **kwargs)
            second = memory_agent.finalize_memory(root, "migrate", "failed", **kwargs)

            runs = list((state / "loop_engineering" / "runs").iterdir())
            candidates = list((root / "memory" / "sessions").glob("*.md"))
            journal = root / "state" / "distill_runs.jsonl"
            journal_lines = journal.read_text(encoding="utf-8").splitlines()
            run_data = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))

        self.assertEqual(first["loop_run"]["run_id"], second["loop_run"]["run_id"])
        self.assertFalse(first["loop_run"]["reused"])
        self.assertTrue(second["loop_run"]["reused"])
        self.assertEqual(first["candidate_count"], 1)
        self.assertEqual(second["candidate_count"], 0)
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(journal_lines), 1)
        self.assertEqual(run_data["schema_version"], 1)
        self.assertEqual(run_data["contract_version"], 2)
        self.assertEqual(run_data["contract_id"], "laos.loop-run.v2")
        self.assertEqual(run_data["candidate_status"], "generated")
        self.assertEqual(run_data["candidate_ids"], run_data["generated_candidate_ids"])
        self.assertEqual(run_data["policy_status"], "review_required")
        self.assertEqual(run_data["approved_policy_ids"], [])

    def test_policy_whitespace_and_duplicates_share_one_idempotent_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            first = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="pass",
                policy_suggestions=["  verify   target  ", "verify target"],
            )
            second = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="pass",
                policy_suggestions=["verify target"],
            )
            run_dir = state / first["loop_run"]["path"]
            policies = (run_dir / "policy_suggestions.md").read_text(encoding="utf-8")
            run_count = len(list((state / "loop_engineering" / "runs").iterdir()))

        self.assertEqual(first["loop_run"]["run_id"], second["loop_run"]["run_id"])
        self.assertEqual(policies.count("- [ ]"), 1)
        self.assertEqual(run_count, 1)

    def test_legacy_v1_run_remains_approvable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root,
                "task",
                "result",
                state_dir=state,
                outcome="pass",
                policy_suggestions=["verify target"],
            )
            run_dir = state / finalized["loop_run"]["path"]
            run_path = run_dir / "run.json"
            run_data = json.loads(run_path.read_text(encoding="utf-8"))
            legacy_fields = {
                "schema_version",
                "run_id",
                "task_sha256",
                "result_sha256",
                "outcome",
                "candidate_ids",
                "reflection_path",
                "policy_suggestions_path",
                "review_required",
                "approved_policy_count",
                "status",
            }
            run_data = {key: value for key, value in run_data.items() if key in legacy_fields}
            run_path.write_text(
                json.dumps(run_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            policy_path = run_dir / "policy_suggestions.md"
            policy_path.write_text(
                policy_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                encoding="utf-8",
            )

            result = memory_agent.approve_policies(
                root,
                state_dir=state,
                run_id=finalized["loop_run"]["run_id"],
                confirmed=True,
            )
            completed = json.loads(run_path.read_text(encoding="utf-8"))
            rules = (root / "memory_rules.md").read_text(encoding="utf-8")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(completed["status"], "completed")
        self.assertNotIn("contract_id", completed)
        self.assertIn("- Policy: verify target", rules)

    def test_v2_contract_rejects_inconsistent_candidate_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            finalized = memory_agent.finalize_memory(
                root, "task", "result", state_dir=state, outcome="pass"
            )
            run_dir = state / finalized["loop_run"]["path"]
            run_path = run_dir / "run.json"
            run_data = json.loads(run_path.read_text(encoding="utf-8"))
            run_data["candidate_status"] = "not_generated"
            run_path.write_text(
                json.dumps(run_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(memory_agent.AgentError) as raised:
                memory_agent.approve_policies(
                    root,
                    state_dir=state,
                    run_id=finalized["loop_run"]["run_id"],
                    confirmed=True,
                )

        self.assertEqual(raised.exception.code, "invalid_loop_run")
        self.assertFalse((root / "memory_rules.md").exists())

    def test_journal_snapshot_read_failure_removes_published_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            journal = root / "state" / "distill_runs.jsonl"
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text('{"existing": true}\n', encoding="utf-8")
            before = journal.read_bytes()
            unreadable_journal = mock.Mock()
            unreadable_journal.exists.return_value = True
            unreadable_journal.read_text.side_effect = OSError(
                "sensitive journal read failure"
            )

            with mock.patch.object(
                memory_agent.memory_distill,
                "_journal_path",
                return_value=unreadable_journal,
            ):
                with self.assertRaises(memory_agent.AgentError) as raised:
                    memory_agent.finalize_memory(
                        root,
                        "task",
                        "result",
                        state_dir=state,
                        outcome="fail",
                    )

            runs_dir = state / "loop_engineering" / "runs"
            run_entries = list(runs_dir.iterdir()) if runs_dir.exists() else []
            candidates = list((root / "memory" / "sessions").glob("*.md"))
            journal_after = journal.read_bytes()

        self.assertEqual(raised.exception.code, "distillation_failed")
        self.assertEqual(run_entries, [])
        self.assertEqual(candidates, [])
        self.assertEqual(journal_after, before)

    def test_blank_result_is_idempotent_and_never_creates_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, state = self.init_root(tmp)
            first = memory_agent.finalize_memory(
                root, "task", "   ", state_dir=state, outcome="pass"
            )
            second = memory_agent.finalize_memory(
                root, "task", "   ", state_dir=state, outcome="pass"
            )
            run_dir = state / first["loop_run"]["path"]
            run_data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            candidates = list((root / "memory" / "sessions").glob("*.md"))

        self.assertEqual(first["loop_run"]["run_id"], second["loop_run"]["run_id"])
        self.assertEqual(run_data["candidate_status"], "not_generated")
        self.assertEqual(run_data["generated_candidate_ids"], [])
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
