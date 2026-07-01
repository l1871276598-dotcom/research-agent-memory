import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from delegation import DelegationManager, KanbanStore
from delegation.manager import BLOCKED_CHILD_TOOLS


class Child:
    def __init__(self, settings):
        self.settings = settings

    def run(self, **kwargs):
        time.sleep(0.01)
        return {
            "content": f"finished:{kwargs['user_message']}",
            "iterations": 1,
            "private": "not returned",
        }


class DelegationTests(unittest.TestCase):
    def test_children_receive_fresh_restricted_scope_and_return_summaries(self):
        created = []

        def factory(**settings):
            created.append(settings)
            return Child(settings)

        manager = DelegationManager(factory, max_concurrent=2)
        results = manager.delegate(
            [
                {
                    "goal": "inspect tests",
                    "context": "Only inspect the current repository.",
                    "allowed_tools": ["read_file", "memory.create", "delegate_task"],
                    "workspace": "work",
                    "project": "laos",
                },
                {"goal": "summarize failures", "allowed_tools": ["search_files"]},
            ]
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["status"] == "completed" for result in results))
        self.assertTrue(all("private" not in result for result in results))
        allowed_sets = {tuple(item["allowed_tools"]) for item in created}
        self.assertEqual(allowed_sets, {("read_file",), ("search_files",)})
        self.assertTrue(
            all(BLOCKED_CHILD_TOOLS.isdisjoint(item["allowed_tools"]) for item in created)
        )
        self.assertEqual(manager.active(), [])

    def test_pause_and_depth_limits_fail_closed(self):
        manager = DelegationManager(lambda **kwargs: Child(kwargs), max_depth=1)
        manager.pause(True)
        with self.assertRaises(RuntimeError):
            manager.delegate([{"goal": "x"}])
        manager.pause(False)
        with self.assertRaises(ValueError):
            manager.delegate([{"goal": "x"}], depth=2)

    def test_kanban_dependencies_claims_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            board = KanbanStore(directory, board="laos")
            parent = board.create("Parent", "Finish foundation", task_id="parent", priority=5)
            child = board.create("Child", "Run integration", task_id="child", priority=10)
            board.link(parent["id"], child["id"])

            ready = board.recompute_ready()
            self.assertEqual(ready, ["parent"])
            claimed = board.claim("worker-one")
            self.assertEqual(claimed["id"], "parent")
            board.heartbeat("parent", "worker-one", ttl_seconds=60)
            board.complete("parent", "worker-one", {"ok": True})

            claimed_child = board.claim("worker-two")
            self.assertEqual(claimed_child["id"], "child")
            blocked = board.block("child", "worker-two", "needs_input", "author decision")
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["block_kind"], "needs_input")

    def test_dependency_and_transient_blocks_do_not_enter_human_blocked_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            board = KanbanStore(directory)
            board.create("Task", "Work", task_id="task", status="ready")
            board.claim("worker")
            dependency = board.block("task", "worker", "dependency", "wait for parent")
            self.assertEqual(dependency["status"], "todo")

            board.recompute_ready()
            board.claim("worker")
            transient = board.block("task", "worker", "transient", "temporary outage")
            self.assertEqual(transient["status"], "ready")


if __name__ == "__main__":
    unittest.main()
