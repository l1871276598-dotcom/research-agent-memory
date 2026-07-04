import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from profiles import ProfileStore, current_profile


class ProfileTests(unittest.TestCase):
    def test_profiles_map_to_workspace_project_and_confidentiality(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            profile = store.create(
                "laos-work",
                workspace="work",
                project="laos",
                confidentiality="internal",
                enabled_extensions=["github"],
                allowed_tools=["memory.search", "context.build"],
            )
            self.assertEqual(profile["workspace"], "work")
            self.assertEqual(profile["project"], "laos")
            self.assertEqual(store.list()[0]["name"], "laos-work")

            scoped = store.task_scope(
                "laos-work",
                {"type": "memory.search", "workspace": "personal", "input": {"query": "x"}},
            )
            self.assertEqual(scoped["workspace"], "work")
            self.assertEqual(scoped["project"], "laos")

    def test_activation_is_context_local_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            store.create("personal", workspace="personal")
            self.assertIsNone(current_profile())
            with store.activate("personal") as profile:
                self.assertEqual(profile["name"], "personal")
                self.assertEqual(current_profile()["workspace"], "personal")
            self.assertIsNone(current_profile())

    def test_invalid_or_duplicate_profiles_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            store.create("valid", workspace="personal")
            with self.assertRaises(ValueError):
                store.create("valid", workspace="personal")
            with self.assertRaises(ValueError):
                store.create("Bad Name", workspace="personal")
            with self.assertRaises(ValueError):
                store.create("other", workspace="unknown")


if __name__ == "__main__":
    unittest.main()
