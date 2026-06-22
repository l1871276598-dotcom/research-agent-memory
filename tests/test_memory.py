import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
CSV_HEADER = "id,doi,title,authors,journal,year,status,pdf_path,note_path,project,tags"

EXPECTED_DIRS = [
    "memory/profile",
    "memory/contexts",
    "memory/transitions",
    "memory/principles",
    "memory/projects",
    "memory/decisions",
    "memory/procedures",
    "memory/sessions",
    "literature/inbox",
    "literature/pdf",
    "literature/notes",
    "literature/journals",
    "manuscripts/current",
    "manuscripts/evidence",
    "manuscripts/archive",
    "exports/database_snapshots",
    "backups",
]


class InitCommandTests(unittest.TestCase):
    def run_init(self, root):
        return subprocess.run(
            [sys.executable, str(MEMORY_CLI), "init", "--root", str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def test_init_creates_expected_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            result = self.run_init(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in EXPECTED_DIRS:
                self.assertTrue((root / relative).is_dir(), relative)

            marker = root / ".research-agent-root"
            manifest = root / "exports" / "index_manifest.json"
            matrix = root / "literature" / "literature_matrix.csv"
            self.assertTrue(marker.is_file())
            self.assertTrue(manifest.is_file())
            self.assertTrue(matrix.is_file())
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8")),
                {"format_version": 1, "type": "research-agent-data-root"},
            )
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8")),
                {"format_version": 1, "records": []},
            )
            self.assertEqual(matrix.read_text(encoding="utf-8").strip(), CSV_HEADER)

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"

            first = self.run_init(root)
            second = self.run_init(root)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)

    def test_init_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data-root"
            manifest = root / "exports" / "index_manifest.json"
            custom = '{"format_version": 99, "records": ["keep"]}'

            self.assertEqual(self.run_init(root).returncode, 0)
            manifest.write_text(custom, encoding="utf-8")
            self.assertEqual(self.run_init(root).returncode, 0)

            self.assertEqual(manifest.read_text(encoding="utf-8"), custom)

    def test_init_rejects_repository_root(self):
        result = self.run_init(REPO_ROOT)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("数据目录不能等于代码仓库目录。", result.stderr)


if __name__ == "__main__":
    unittest.main()
