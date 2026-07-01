import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from reflection.review_executor import ReviewExecutor


class Coordinator:
    def __init__(self):
        self.calls = []

    def record_turn(self, **turn):
        self.calls.append(turn)
        time.sleep(0.05)
        return {"status": "reviewed", "session_id": turn["session_id"]}


class ReviewExecutorTests(unittest.TestCase):
    def test_same_session_is_not_submitted_twice(self):
        coordinator = Coordinator()
        executor = ReviewExecutor(coordinator)
        try:
            first = executor.submit(session_id="session-1")
            second = executor.submit(session_id="session-1")
            result = executor.result("session-1", timeout=1)
            self.assertEqual(first["status"], "scheduled")
            self.assertEqual(second["status"], "already_running")
            self.assertEqual(result["status"], "reviewed")
            self.assertEqual(len(coordinator.calls), 1)
        finally:
            executor.close()


if __name__ == "__main__":
    unittest.main()
