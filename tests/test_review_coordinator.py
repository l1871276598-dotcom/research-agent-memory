import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from reflection import ConversationReviewCoordinator, ReviewStateStore


class FakeService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def review(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return {
            "candidate_ids": ["candidate-1"],
            "skill_candidates": [],
            "nothing_to_save": False,
        }


class ReviewCoordinatorTests(unittest.TestCase):
    def test_state_persists_and_resets_reviewed_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReviewStateStore(directory)
            state.advance("session-1", 2)
            state.advance("session-1", 1)
            self.assertEqual(state.get("session-1")["turns"], 2)
            self.assertEqual(state.get("session-1")["tool_iterations"], 3)

            state.complete(
                "session-1",
                memory_reviewed=True,
                skills_reviewed=False,
            )
            row = ReviewStateStore(directory).get("session-1")
            self.assertEqual(row["turns"], 0)
            self.assertEqual(row["tool_iterations"], 3)
            self.assertIsNotNone(row["last_reviewed_at"])

    def test_coordinator_waits_for_threshold_then_reviews_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeService()
            state = ReviewStateStore(directory)
            coordinator = ConversationReviewCoordinator(
                service,
                state,
                memory_interval=2,
                skill_interval=3,
            )
            common = {
                "session_id": "session-1",
                "messages": [{"role": "user", "content": "hello"}],
                "workspace": "personal",
            }

            first = coordinator.record_turn(**common, tool_iterations=1)
            second = coordinator.record_turn(**common, tool_iterations=2)

            self.assertEqual(first["status"], "not_due")
            self.assertEqual(second["status"], "reviewed")
            self.assertTrue(second["review_memory"])
            self.assertTrue(second["review_skills"])
            self.assertEqual(len(service.calls), 1)
            options = service.calls[0][1]
            self.assertTrue(options["routed"])
            self.assertEqual(options["session_id"], "session-1")
            self.assertEqual(second["counters"]["turns"], 0)
            self.assertEqual(second["counters"]["tool_iterations"], 0)

    def test_failed_review_is_isolated_and_remains_due(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReviewStateStore(directory)
            coordinator = ConversationReviewCoordinator(
                FakeService(RuntimeError("review unavailable")),
                state,
                memory_interval=1,
                skill_interval=10,
            )

            result = coordinator.record_turn(
                session_id="session-1",
                messages=[{"role": "user", "content": "hello"}],
                workspace="personal",
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["counters"]["turns"], 1)
            self.assertEqual(result["counters"]["last_error"], "review unavailable")

    def test_restricted_conversation_never_advances_or_calls_model(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeService()
            state = ReviewStateStore(directory)
            coordinator = ConversationReviewCoordinator(service, state, memory_interval=1)

            result = coordinator.record_turn(
                session_id="restricted-1",
                messages=[{"role": "user", "content": "private"}],
                workspace="work",
                confidentiality="restricted",
            )

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(state.get("restricted-1")["turns"], 0)
            self.assertEqual(service.calls, [])

    def test_force_reviews_both_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeService()
            coordinator = ConversationReviewCoordinator(
                service,
                ReviewStateStore(directory),
                memory_interval=100,
                skill_interval=100,
            )

            result = coordinator.record_turn(
                session_id="session-force",
                messages=[{"role": "user", "content": "remember"}],
                workspace="personal",
                force=True,
            )

            self.assertEqual(result["status"], "reviewed")
            self.assertTrue(result["review_memory"])
            self.assertTrue(result["review_skills"])


if __name__ == "__main__":
    unittest.main()
