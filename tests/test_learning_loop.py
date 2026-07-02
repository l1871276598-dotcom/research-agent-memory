import argparse
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.learning_loop import LearningLoop
from src.memory import db_init, index_store, init_store
from src.memory.candidate import CandidateStore
from src.memory.store import MemoryStore
from src.review.gate import ReviewGate


class ImprovingBackend:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools=None, **options):
        self.calls += 1
        prompt = "\n".join(item["content"] for item in messages)
        ids = re.findall(r"^id: ([^\s]+)$", prompt, flags=re.MULTILINE)
        if ids:
            return {
                "content": (
                    "# Audit\n\nMemory used:\n"
                    + "\n".join(f"- {memory_id}" for memory_id in ids)
                    + "\n\nFindings: idempotency, fail-closed behavior, recovery."
                )
            }
        return {"content": "# Audit\n\nMemory used: none\n\nFinding: idempotency."}


class FailingOnceBackend:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools=None, **options):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated interruption")
        return {"content": "Memory used: none\n\nFinding: safe."}


def task(run_id, target, *, workspace="personal", query=None, criteria=None):
    return {
        "run_id": run_id,
        "task_id": f"audit-{run_id}",
        "type": "repository.module_audit",
        "title": f"Audit {target}",
        "instruction": "Audit the module and cite concrete evidence.",
        "query": query or "idempotency fail-closed recovery module audit",
        "workspace": workspace,
        "confidentiality": "personal" if workspace == "personal" else "internal",
        "inputs": [target],
        "minimum_score": 1.0,
        "criteria": criteria
        or [
            {
                "id": "idempotency",
                "description": "verify idempotency",
                "required_terms": ["idempotency"],
                "strategy": "For module audits, verify idempotency and cite the replay key.",
            },
            {
                "id": "fail_closed",
                "description": "verify fail-closed behavior",
                "required_terms": ["fail-closed"],
                "strategy": (
                    "For module audits, verify fail-closed behavior before treating "
                    "a capability as available."
                ),
            },
            {
                "id": "recovery",
                "description": "verify interruption recovery",
                "required_terms": ["recovery"],
                "strategy": "For module audits, verify recovery after interruption.",
            },
        ],
    }


class LearningLoopTests(unittest.TestCase):
    def test_cli_initializes_empty_data_root(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "learning_loop.py"),
                    "--data-root",
                    data,
                    "--state-dir",
                    state,
                    "--work-root",
                    str(ROOT),
                    "status",
                    "--run-id",
                    "missing-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotIn("请先执行", result.stderr)
            self.assertTrue(Path(data, ".research-agent-root").is_file())

    def test_cli_script_starts_without_pythonpath(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "learning_loop.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.work = tempfile.TemporaryDirectory()
        init_store(self.data.name)
        db_init(argparse.Namespace(root=self.data.name, state_dir=self.state.name))
        index_store(
            argparse.Namespace(
                root=self.data.name,
                state_dir=self.state.name,
                dry_run=False,
            )
        )
        Path(self.work.name, "module_one.py").write_text(
            "def save(key, value):\n    return value\n",
            encoding="utf-8",
        )
        Path(self.work.name, "module_two.py").write_text(
            "def project(event):\n    return event\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.work.cleanup()
        self.state.cleanup()
        self.data.cleanup()

    def test_end_to_end_learning_loop_reuses_only_accepted_memory(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        first = loop.execute(task("run-one", "module_one.py"))
        self.assertEqual(first["state"], "review_pending")
        self.assertEqual(first["score"], 1 / 3)
        self.assertEqual(len(first["candidate_ids"]), 2)
        first_run_path = Path(first["artifacts"]["run.json"])
        first_run_bytes = first_run_path.read_bytes()

        pending_replay = loop.execute(task("run-one", "module_one.py"))
        self.assertEqual(pending_replay["candidate_ids"], first["candidate_ids"])
        self.assertEqual(first_run_path.read_bytes(), first_run_bytes)
        self.assertEqual(backend.calls, 1)

        store = MemoryStore(self.data.name)
        self.assertEqual(
            store.active_relevant(
                "fail-closed recovery module audit",
                workspace="personal",
            ),
            [],
        )

        accepted_id, rejected_id = first["candidate_ids"]
        loop.review("run-one", accepted_id, "accept", reason="reusable audit rule")
        reviewed = loop.review("run-one", rejected_id, "reject", reason="too broad")
        self.assertEqual(reviewed["state"], "reviewed")

        active = store.active_relevant(
            "fail-closed module audit",
            workspace="personal",
        )
        self.assertEqual([item["id"] for item in active], [accepted_id])
        self.assertNotEqual(
            MemoryStore(self.data.name).get(rejected_id)["status"],
            "active",
        )

        second_task = task(
            "run-two",
            "module_two.py",
            query="fail-closed idempotency recovery module audit",
        )
        second_task["baseline_run_id"] = "run-one"
        second = loop.execute(second_task)
        self.assertEqual(second["state"], "completed")
        self.assertEqual(second["score"], 1.0)

        context = json.loads(
            Path(second["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        result = Path(second["artifacts"]["result.md"]).read_text(encoding="utf-8")
        self.assertIn(accepted_id, context["sources"])
        self.assertNotIn(rejected_id, context["sources"])
        self.assertIn(accepted_id, result)
        self.assertEqual(context["restricted_source_count"], 0)

        comparison = loop.compare("run-one", "run-two", minimum_improvement=0.2)
        self.assertTrue(comparison["passed"])
        self.assertGreaterEqual(comparison["score_delta"], 0.2)
        self.assertEqual(comparison["accepted_in_context"], [accepted_id])
        self.assertEqual(comparison["accepted_in_result"], [accepted_id])
        self.assertEqual(comparison["rejected_in_context"], [])
        self.assertEqual(loop.status("run-two")["state"], "verified")

        replay = loop.execute(task("run-one", "module_one.py"))
        self.assertEqual(replay["candidate_ids"], first["candidate_ids"])
        self.assertEqual(backend.calls, 2)

        run_dir = Path(self.state.name, "learning_runs", "run-one")
        self.assertTrue((run_dir / "run.json").is_file())
        self.assertTrue((run_dir / "outcome.json").is_file())
        self.assertTrue((run_dir / "reflection.md").is_file())
        self.assertTrue((run_dir / "policy_suggestions.md").is_file())
        self.assertTrue((run_dir / "memory_rules.md").is_file())
        self.assertFalse(any(path.suffix == ".tmp" for path in run_dir.iterdir()))

    def test_interrupted_execution_resumes_without_partial_artifacts(self):
        backend = FailingOnceBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        value = task(
            "recover-run",
            "module_one.py",
            query="safe recovery",
            criteria=[
                {
                    "id": "safe",
                    "description": "report a safe result",
                    "required_terms": ["safe"],
                }
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            loop.execute(value)
        interrupted = loop.status("recover-run")
        self.assertEqual(interrupted["state"], "interrupted")
        self.assertIn("context.json", interrupted["artifacts"])
        self.assertNotIn("result.md", interrupted["artifacts"])

        resumed = loop.execute(value)
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(backend.calls, 2)
        self.assertIn("result.md", resumed["artifacts"])
        self.assertIn("evidence.json", resumed["artifacts"])
        self.assertIn("outcome.json", resumed["artifacts"])

    def test_restricted_memory_is_never_injected(self):
        candidates = CandidateStore(self.data.name, self.state.name)
        candidate = candidates.create(
            {
                "type": "principle",
                "title": "Restricted audit strategy",
                "scope": "global",
                "workspace": "work",
                "confidentiality": "restricted",
                "source": "test",
                "content": "restricted audit strategy secret",
                "action": "create",
                "confidence": "confirmed",
                "source_id": "test:restricted:audit",
                "evidence": ["test"],
                "source_refs": ["test:fixture"],
                "tags": ["restricted", "audit"],
            }
        )
        ReviewGate(self.data.name, self.state.name).review(
            "accept",
            candidate["candidate_id"],
            workspace="work",
        )

        backend = FailingOnceBackend()
        backend.calls = 1
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        value = task(
            "restricted-check",
            "module_one.py",
            workspace="work",
            query="restricted audit strategy secret",
            criteria=[
                {
                    "id": "safe",
                    "description": "report a safe result",
                    "required_terms": ["safe"],
                }
            ],
        )
        result = loop.execute(value)
        context = json.loads(
            Path(result["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        self.assertEqual(context["sources"], [])
        self.assertEqual(context["source_records"], [])
        self.assertEqual(context["restricted_source_count"], 0)

    def test_review_replay_is_idempotent(self):
        loop = LearningLoop(
            self.data.name,
            self.state.name,
            self.work.name,
            ImprovingBackend(),
        )
        first = loop.execute(task("review-replay", "module_one.py"))
        candidate_id = first["candidate_ids"][0]
        accepted = loop.review("review-replay", candidate_id, "accept")
        replay = loop.review("review-replay", candidate_id, "accept")
        self.assertEqual(
            accepted["review_decisions"][candidate_id],
            replay["review_decisions"][candidate_id],
        )
        with self.assertRaisesRegex(ValueError, "different review decision"):
            loop.review("review-replay", candidate_id, "reject")

    def test_prompt_requires_explicit_active_strategy_application(self):
        messages = LearningLoop._prompt(
            task("prompt-check", "module_one.py"),
            {
                "text": "id: principle-1\ncontent: Produce a named evidence map.",
                "sources": ["principle-1"],
            },
            "module source",
        )

        self.assertIn(
            "Apply every supplied active-memory strategy explicitly",
            messages[0]["content"],
        )


if __name__ == "__main__":
    unittest.main()
