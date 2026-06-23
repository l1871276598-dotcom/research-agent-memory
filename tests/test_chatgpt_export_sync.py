import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
SYNC_CLI = REPO_ROOT / "src" / "chatgpt_export_sync.py"


class ChatGPTExportSyncTests(unittest.TestCase):
    def run_cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )

    def init_store(self, root, state_dir):
        self.assertEqual(self.run_cli(MEMORY_CLI, "init", "--root", str(root)).returncode, 0)
        self.assertEqual(self.run_cli(MEMORY_CLI, "db-init", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)

    def make_zip(self, path, text="第一条消息", title="研究讨论"):
        conversations = [
            {
                "id": "conv-1",
                "title": title,
                "create_time": "2026-05-01T00:00:00",
                "update_time": "2026-05-01T00:00:00",
                "messages": [{"author": "user", "text": text}],
            }
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("conversations.json", json.dumps(conversations, ensure_ascii=False))

    def db_rows(self, db, sql, params=()):
        with sqlite3.connect(db) as conn:
            return conn.execute(sql, params).fetchall()

    def test_import_zip_archives_raw_generates_recent_and_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            zip_path = Path(tmp) / "chatgpt.zip"
            self.init_store(root, state_dir)
            self.make_zip(zip_path)

            result = self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path), "--json")
            summary = json.loads(result.stdout)
            raw = sorted((root / "imports" / "chatgpt" / "conversations").rglob("*.md"))
            recent = root / "memory" / "recent" / "recent-chatgpt-conv-1.md"
            rows = self.db_rows(state_dir / "memory.sqlite", "SELECT id, source_path, source_sha256 FROM memories WHERE id = 'recent-chatgpt-conv-1'")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(summary["imported"], 1)
            self.assertEqual(len(raw), 1)
            self.assertTrue(recent.exists())
            self.assertEqual(rows[0][1], raw[0].relative_to(root).as_posix())
            self.assertEqual(rows[0][2], hashlib.sha256(raw[0].read_bytes()).hexdigest())

    def test_import_zip_is_idempotent_and_does_not_reset_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            zip_path = Path(tmp) / "chatgpt.zip"
            self.init_store(root, state_dir)
            self.make_zip(zip_path)
            self.assertEqual(self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path)).returncode, 0)
            recent = root / "memory" / "recent" / "recent-chatgpt-conv-1.md"
            before = recent.read_text(encoding="utf-8")
            before_hash = hashlib.sha256(recent.read_bytes()).hexdigest()

            result = self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path), "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["unchanged"], 1)
            self.assertEqual(hashlib.sha256(recent.read_bytes()).hexdigest(), before_hash)
            self.assertEqual(recent.read_text(encoding="utf-8"), before)

    def test_import_zip_updates_recent_when_conversation_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            zip_path = Path(tmp) / "chatgpt.zip"
            self.init_store(root, state_dir)
            self.make_zip(zip_path)
            self.assertEqual(self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path)).returncode, 0)
            self.make_zip(zip_path, text="更新后的消息")

            result = self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path), "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["updated"], 1)
            self.assertIn("更新后的消息", (root / "memory" / "recent" / "recent-chatgpt-conv-1.md").read_text(encoding="utf-8"))

    def test_import_zip_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            zip_path = Path(tmp) / "chatgpt.zip"
            self.init_store(root, state_dir)
            self.make_zip(zip_path)

            result = self.run_cli(SYNC_CLI, "import-zip", "--root", str(root), "--state-dir", str(state_dir), "--zip", str(zip_path), "--dry-run", "--json")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(list((root / "imports" / "chatgpt" / "conversations").rglob("*.md")))
            self.assertFalse(list((root / "memory" / "recent").glob("recent-chatgpt-*.md")))


if __name__ == "__main__":
    unittest.main()
