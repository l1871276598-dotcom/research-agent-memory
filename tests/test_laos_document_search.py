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


class LaosDocumentSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def tearDown(self):
        self.temp.cleanup()

    def write_document(self, name, *, confidentiality="personal", project=None):
        path = self.root / "imports/chatgpt/conversations" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = [
            "---",
            f'title: "{name}"',
            'workspace: "personal"',
            f'confidentiality: "{confidentiality}"',
        ]
        if project is not None:
            metadata.append(f'project: "{project}"')
        metadata.extend(["---", "indexed bridge phrase"])
        path.write_text("\n".join(metadata) + "\n", encoding="utf-8")

    def run_search(self, *, project=None):
        task = {
            "type": "memory.search",
            "input": {
                "query": "indexed bridge phrase",
                "workspace": "personal",
            },
        }
        if project is not None:
            task["input"]["project"] = project
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

    def test_agent_search_includes_visible_indexed_documents_without_partition_leaks(self):
        self.write_document("visible-document.md")
        self.write_document("restricted-document.md", confidentiality="restricted")
        self.write_document("project-document.md", project="project-one")
        memory.index_store(
            argparse.Namespace(root=str(self.root), state_dir=str(self.state), dry_run=False)
        )

        unscoped = self.run_search()
        scoped = self.run_search(project="project-one")

        self.assertEqual(unscoped.returncode, 0, unscoped.stderr)
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        unscoped_rows = json.loads(unscoped.stdout)["output"]["results"]
        scoped_rows = json.loads(scoped.stdout)["output"]["results"]
        self.assertEqual(
            [(row["title"], row["kind"]) for row in unscoped_rows],
            [("visible-document.md", "document")],
        )
        self.assertEqual(
            {(row["title"], row["kind"]) for row in scoped_rows},
            {
                ("visible-document.md", "document"),
                ("project-document.md", "document"),
            },
        )


if __name__ == "__main__":
    unittest.main()
