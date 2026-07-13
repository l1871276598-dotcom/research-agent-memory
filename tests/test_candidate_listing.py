import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory


def write_record(root, **overrides):
    record = {
        "id": "candidate-default",
        "type": "session",
        "title": "Candidate default",
        "created": "2026-07-06",
        "updated": "2026-07-06",
        "status": "candidate",
        "scope": "global",
        "workspace": "personal",
        "confidentiality": "personal",
        "source": "test",
        "confidence": "confirmed",
        "content": "candidate content",
        "tags": [],
    }
    record.update(overrides)
    directory = root / memory.TYPE_DIRS[record["type"]]
    path = directory / f"{record['id']}-test.md"
    path.write_text(memory.render_memory(record), encoding="utf-8")
    return path


class CandidateListingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))
        write_record(
            self.root,
            id="active-project",
            type="project",
            title="Local operations",
            status="active",
            scope="project",
            project="localoperations",
            content="active project anchor",
        )
        write_record(
            self.root,
            id="candidate-global",
            title="Global candidate",
            content="global candidate content",
            source_refs=["test:global"],
        )
        write_record(
            self.root,
            id="candidate-project",
            title="Project candidate",
            scope="project",
            project="localoperations",
            content="project candidate content",
            candidate_action="ADD",
        )
        write_record(
            self.root,
            id="candidate-conflicted",
            title="Conflicted candidate",
            status="conflicted",
            content="conflicted candidate content",
        )
        write_record(
            self.root,
            id="candidate-archived",
            title="Archived candidate",
            status="archived",
            content="archived candidate content",
        )
        write_record(
            self.root,
            id="candidate-active",
            title="Active memory",
            status="active",
            content="active content",
        )
        write_record(
            self.root,
            id="candidate-restricted",
            title="Restricted candidate",
            workspace="work",
            confidentiality="restricted",
            content="restricted content",
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_task(self, task):
        return subprocess.run(
            [
                sys.executable,
                str(SOURCE_DIR / "laos.py"),
                "--root",
                str(self.root),
                "--state-dir",
                str(self.state),
                "--task-json",
                json.dumps(task, ensure_ascii=False, separators=(",", ":")),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_store_lists_reviewable_candidates_without_restricted_records(self):
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        personal = store.reviewable("personal")
        work = store.reviewable("work")

        self.assertEqual(
            [record["id"] for record in personal],
            ["candidate-conflicted", "candidate-global", "candidate-project"],
        )
        self.assertEqual(work, [])
        self.assertEqual(
            [record["id"] for record in store.reviewable("personal", "localoperations")],
            ["candidate-project"],
        )
        self.assertEqual(
            [record["id"] for record in store.reviewable("personal", statuses=["candidate"])],
            ["candidate-global", "candidate-project"],
        )

    def test_cli_memory_candidates_is_read_only_and_metadata_bounded(self):
        result = self.run_task(
            {
                "type": "memory.candidates",
                "workspace": "personal",
                "input": {"limit": 2},
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["agent_id"], "candidate_list_agent")
        self.assertEqual(payload["candidates"], [])
        rows = payload["output"]["results"]
        self.assertEqual([row["id"] for row in rows], ["candidate-conflicted", "candidate-global"])
        self.assertEqual(rows[0]["status"], "conflicted")
        self.assertIn("content_preview", rows[0])
        self.assertNotIn("content", rows[0])
        self.assertEqual(rows[1]["source_refs"], ["test:global"])

    def test_cli_memory_candidates_filters_status_and_project(self):
        result = self.run_task(
            {
                "type": "memory.candidates",
                "input": {
                    "workspace": "personal",
                    "project": "localoperations",
                    "status": "candidate",
                },
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)["output"]["results"]
        self.assertEqual([row["id"] for row in rows], ["candidate-project"])
        self.assertEqual(rows[0]["candidate_action"], "ADD")


if __name__ == "__main__":
    unittest.main()
