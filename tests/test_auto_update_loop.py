import argparse
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.learning_loop import AutoUpdateLoopCoordinator, LearningLoop
from src.memory import db_init, index_store, init_store
from src.memory.store import MemoryStore


class Backend:
    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on

    def complete(self, messages, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("simulated interruption")
        prompt = "\n".join(item["content"] for item in messages)
        ids = re.findall(r"^id: (\S+)$", prompt, flags=re.MULTILINE)
        if ids:
            return {"content": "Memory used:\n" + "\n".join(ids) + "\nidempotency fail-closed recovery"}
        return {"content": "Memory used: none\nidempotency"}


class PassingBackend:
    def complete(self, messages, **kwargs):
        return {"content": "Memory used: none\nidempotency fail-closed recovery"}


def make_task(run_id, target, baseline=None):
    value = {
        "run_id": run_id,
        "task_id": run_id,
        "type": "repository.module_audit",
        "title": f"Audit {target}",
        "instruction": "Audit the module.",
        "query": "idempotency fail-closed recovery audit",
        "workspace": "personal",
        "confidentiality": "personal",
        "inputs": [target],
        "minimum_score": 1.0,
        "criteria": [
            {"id": "idempotency", "description": "verify idempotency", "required_terms": ["idempotency"]},
            {"id": "fail_closed", "description": "verify fail closed", "required_terms": ["fail-closed"]},
            {"id": "recovery", "description": "verify recovery", "required_terms": ["recovery"]},
        ],
    }
    if baseline:
        value["baseline_run_id"] = baseline
    return value


class AutoUpdateLoopTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.work = tempfile.TemporaryDirectory()
        init_store(self.data.name)
        db_init(argparse.Namespace(root=self.data.name, state_dir=self.state.name))
        index_store(argparse.Namespace(root=self.data.name, state_dir=self.state.name, dry_run=False))
        Path(self.work.name, "one.py").write_text("value = 1\n", encoding="utf-8")
        Path(self.work.name, "two.py").write_text("value = 2\n", encoding="utf-8")

    def tearDown(self):
        self.work.cleanup()
        self.state.cleanup()
        self.data.cleanup()

    def build(self, backend):
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        return loop, AutoUpdateLoopCoordinator(loop)

    def test_waits_for_review_then_verifies(self):
        backend = Backend()
        loop, coordinator = self.build(backend)
        baseline = make_task("auto-one", "one.py")
        pending = coordinator.advance(baseline)
        self.assertEqual(pending["state"], "awaiting_review")
        self.assertEqual([x["state"] for x in pending["state_history"]], [
            "task_created", "context_built", "execution_completed", "outcome_recorded",
            "reflection_created", "candidate_created", "awaiting_review",
        ])
        self.assertEqual(MemoryStore(self.data.name).active_relevant("fail-closed recovery", workspace="personal"), [])
        accepted, rejected = pending["candidate_ids"]
        loop.review("auto-one", accepted, "accept")
        loop.review("auto-one", rejected, "reject")
        verification = make_task("auto-two", "two.py", "auto-one")
        result = coordinator.advance(baseline, verification_task=verification)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["active_memory_ids"], [accepted])
        self.assertTrue(result["comparison_passed"])
        self.assertEqual(backend.calls, 2)
        replay = coordinator.advance(baseline, verification_task=verification)
        self.assertEqual(replay["state_history"], result["state_history"])
        self.assertEqual(backend.calls, 2)

    def test_all_rejected_stops_without_activation(self):
        loop, coordinator = self.build(Backend())
        baseline = make_task("reject-one", "one.py")
        pending = coordinator.advance(baseline)
        for candidate_id in pending["candidate_ids"]:
            loop.review("reject-one", candidate_id, "reject")
        result = coordinator.advance(baseline)
        self.assertEqual(result["state"], "rejected")
        self.assertNotIn("active_memory_ids", result)
        self.assertNotIn("verification_run_id", result)

    def test_interrupted_baseline_resumes_idempotently(self):
        backend = Backend(fail_on=1)
        _, coordinator = self.build(backend)
        baseline = make_task("recover-one", "one.py")
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            coordinator.advance(baseline)
        self.assertEqual(coordinator.status("recover-one")["state"], "context_built")
        result = coordinator.advance(baseline)
        self.assertEqual(result["state"], "awaiting_review")
        states = [x["state"] for x in result["state_history"]]
        self.assertEqual(len(states), len(set(states)))
        self.assertEqual(backend.calls, 2)

    def test_interrupted_verification_resumes_from_schedule(self):
        backend = Backend(fail_on=2)
        loop, coordinator = self.build(backend)
        baseline = make_task("verify-one", "one.py")
        pending = coordinator.advance(baseline)
        accepted, rejected = pending["candidate_ids"]
        loop.review("verify-one", accepted, "accept")
        loop.review("verify-one", rejected, "reject")
        verification = make_task("verify-two", "two.py", "verify-one")
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            coordinator.advance(baseline, verification_task=verification)
        self.assertEqual(coordinator.status("verify-one")["state"], "verification_scheduled")
        result = coordinator.advance(baseline)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(backend.calls, 3)

    def test_no_candidate_is_no_update_success(self):
        _, coordinator = self.build(PassingBackend())
        result = coordinator.advance(make_task("no-update", "one.py"))
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["verification_reason"], "no_update_required")
        self.assertEqual(result["candidate_ids"], [])


if __name__ == "__main__":
    unittest.main()
