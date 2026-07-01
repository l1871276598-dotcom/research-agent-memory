import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sessions import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_append_end_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            created = store.create(
                session_id="session-one",
                workspace="work",
                project="laos",
                title="LAOS review",
            )
            self.assertEqual(created["id"], "session-one")
            store.append("session-one", "user", "Remember the evidence chain.")
            store.append("session-one", "assistant", "I will create a candidate first.")
            ended = store.end("session-one")
            self.assertEqual(ended["end_reason"], "completed")
            restored = store.get("session-one")
            self.assertEqual([m["role"] for m in restored["messages"]], ["user", "assistant"])
            with self.assertRaises(ValueError):
                store.append("session-one", "user", "late")

    def test_fts_search_is_scoped_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.create(session_id="personal", workspace="personal")
            store.append("personal", "user", "PDC impact response")
            store.create(session_id="work", workspace="work", project="laos")
            store.append("work", "assistant", "PDC memory candidate")

            personal = store.search("PDC", workspace="personal")
            work = store.search("PDC candidate", workspace="work", project="laos")
            self.assertEqual([row["session_id"] for row in personal], ["personal"])
            self.assertEqual([row["session_id"] for row in work], ["work"])
            with self.assertRaises(ValueError):
                store.search("!@#$")

    def test_branch_copies_history_and_preserves_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.create(session_id="parent", workspace="personal")
            store.append("parent", "user", "original")
            child = store.branch("parent", new_session_id="child")
            self.assertEqual(child["parent_session_id"], "parent")
            self.assertEqual(child["metadata"]["branched_from"], "parent")
            self.assertEqual(child["messages"][0]["content"], "original")
            store.append("child", "assistant", "branched answer")
            self.assertEqual(len(store.get("parent")["messages"]), 1)
            self.assertEqual(len(store.get("child")["messages"]), 2)

    def test_sync_turn_writes_exactly_two_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.create(session_id="sync", workspace="personal")
            result = store.sync_turn(
                {"session_id": "sync", "user": "question", "assistant": "answer"}
            )
            self.assertEqual(len(result["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
