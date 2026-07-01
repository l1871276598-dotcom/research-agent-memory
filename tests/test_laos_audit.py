import argparse
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory
from laos import build_application
from memory.store import MemoryStore


class ReviewWorkspaceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(
            argparse.Namespace(
                root=str(self.root),
                state_dir=str(self.state),
            )
        )
        self.application = build_application(self.root, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def create_candidate(
        self,
        *,
        workspace="personal",
        confidentiality="personal",
        title="Audit candidate",
    ):
        result = self.application.run(
            {
                "type": "memory.create",
                "workspace": workspace,
                "input": {
                    "type": "principle",
                    "title": title,
                    "scope": "global",
                    "confidentiality": confidentiality,
                    "source": "manual:user_confirmed",
                    "confidence": "confirmed",
                    "content": f"{title} content",
                },
            }
        )
        return result["candidates"][0]

    def status(self, candidate_id):
        return MemoryStore(self.root).get(candidate_id)["status"]

    def test_review_cannot_cross_workspace(self):
        candidate_id = self.create_candidate(
            workspace="work",
            confidentiality="internal",
            title="Work audit candidate",
        )

        with self.assertRaises(ValueError):
            self.application.run(
                {
                    "type": "memory.review",
                    "input": {
                        "action": "accept",
                        "candidate_id": candidate_id,
                    },
                }
            )

        self.assertEqual(self.status(candidate_id), "candidate")

        self.application.run(
            {
                "type": "memory.review",
                "workspace": "work",
                "input": {
                    "action": "accept",
                    "candidate_id": candidate_id,
                },
            }
        )

        self.assertEqual(self.status(candidate_id), "active")

    def test_repeated_accept_does_not_duplicate_active_memory(self):
        candidate_id = self.create_candidate(title="Repeated accept")

        self.application.run(
            {
                "type": "memory.review",
                "input": {
                    "action": "accept",
                    "candidate_id": candidate_id,
                },
            }
        )

        with self.assertRaises(ValueError):
            self.application.run(
                {
                    "type": "memory.review",
                    "input": {
                        "action": "accept",
                        "candidate_id": candidate_id,
                    },
                }
            )

        self.assertEqual(self.status(candidate_id), "active")

    def test_rejected_candidate_cannot_be_accepted(self):
        candidate_id = self.create_candidate(title="Rejected candidate")

        self.application.run(
            {
                "type": "memory.review",
                "input": {
                    "action": "reject",
                    "candidate_id": candidate_id,
                    "reason": "not accepted",
                },
            }
        )

        with self.assertRaises(ValueError):
            self.application.run(
                {
                    "type": "memory.review",
                    "input": {
                        "action": "accept",
                        "candidate_id": candidate_id,
                    },
                }
            )

        self.assertEqual(self.status(candidate_id), "archived")

    def test_invalid_review_action_changes_nothing(self):
        candidate_id = self.create_candidate(title="Invalid action")

        with self.assertRaises(ValueError):
            self.application.run(
                {
                    "type": "memory.review",
                    "input": {
                        "action": "unknown",
                        "candidate_id": candidate_id,
                    },
                }
            )

        self.assertEqual(self.status(candidate_id), "candidate")


if __name__ == "__main__":
    unittest.main()
