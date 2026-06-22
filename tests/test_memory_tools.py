import argparse
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location("memory_tools", SRC / "memory_tools.py")
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class MemoryToolsTests(unittest.TestCase):
    def make_root(self, base):
        root = base / "data"
        root.mkdir()
        (root / ".research-agent-root").write_text(
            json.dumps({"format_version": 1, "type": "research-agent-data-root"}),
            encoding="utf-8",
        )
        return root

    def make_database(self, state):
        state.mkdir()
        db = state / "memory.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
                    content TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL, scope TEXT NOT NULL, workspace TEXT NOT NULL,
                    confidentiality TEXT NOT NULL, source TEXT NOT NULL,
                    confidence TEXT NOT NULL, context_id TEXT, project TEXT,
                    valid_from TEXT, valid_until TEXT, created TEXT NOT NULL,
                    updated TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL
                );
                CREATE INDEX idx_memories_type ON memories(type);
                CREATE INDEX idx_memories_project ON memories(project);
                CREATE INDEX idx_memories_context ON memories(context_id);
                CREATE INDEX idx_memories_workspace_status ON memories(workspace, status);
                CREATE VIRTUAL TABLE memory_fts USING fts5(id UNINDEXED, title, content, tags, tokenize='unicode61');
                CREATE TABLE index_state (
                    relative_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL, indexed_at TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )
            row = (
                "principle-1", "principle", "代码最少原则", "使用尽可能少的代码实现相同功能。",
                '["coding"]', "active", "global", "personal", "personal", "user", "confirmed",
                None, None, None, None, "2026-06-22", "2026-06-22",
                "memory/principles/principle-1.md", "abc",
            )
            conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            conn.execute(
                "INSERT INTO memory_fts VALUES (?,?,?,?)",
                ("principle-1", "代码最少原则 代码 码最 最少 少原 原则", "使用尽可能少的代码实现相同功能 使用 使用 尽可 可能 能少 少的 的代 代码 码实 实现 现相 相同 同功 功能", "coding"),
            )
            conn.execute(
                "INSERT INTO index_state VALUES (?,?,?,?)",
                ("memory/principles/principle-1.md", "abc", 1, "2026-06-22T00:00:00+00:00"),
            )
        return db

    def test_chinese_partial_query_uses_bigrams(self):
        self.assertEqual(TOOLS._fts_query("代码最少"), '"代码" AND "码最" AND "最少"')

    def test_search_returns_chinese_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            state = base / "state"
            self.make_database(state)
            args = argparse.Namespace(
                root=str(root), state_dir=str(state), query="代码最少", type=None,
                project=None, workspace=None, status=None, limit=20, json=True,
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(TOOLS.search(args), 1)
            self.assertEqual(json.loads(output.getvalue())[0]["id"], "principle-1")

    def sample_export(self, path, title="测试 对话"):
        conversations = [
            {
                "id": "conversation-1",
                "title": title,
                "create_time": 1710000000,
                "update_time": 1710000300,
                "current_node": "a2",
                "mapping": {
                    "root": {"parent": None, "message": None},
                    "u1": {
                        "parent": "root",
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1710000000,
                            "content": {"parts": ["请记录这次讨论。"]},
                        },
                    },
                    "a2": {
                        "parent": "u1",
                        "message": {
                            "author": {"role": "assistant"},
                            "create_time": 1710000001,
                            "content": {"parts": ["已经整理。"]},
                        },
                    },
                },
            }
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("export/conversations.json", json.dumps(conversations, ensure_ascii=False))

    def test_import_chatgpt_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), zip=str(export), dry_run=False)
            first = StringIO()
            with redirect_stdout(first):
                TOOLS.import_chatgpt(args)
            self.assertIn("Created: 1", first.getvalue())
            second = StringIO()
            with redirect_stdout(second):
                TOOLS.import_chatgpt(args)
            self.assertIn("Unchanged: 1", second.getvalue())
            self.sample_export(export, title="重命名对话")
            changed = StringIO()
            with redirect_stdout(changed):
                TOOLS.import_chatgpt(args)
            self.assertIn("Updated: 1", changed.getvalue())
            files = list((root / "imports/chatgpt/conversations").rglob("*.md"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("# 重命名对话", text)
            self.assertIn("## User", text)
            self.assertIn("## Assistant", text)

    def test_import_chatgpt_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), zip=str(export), dry_run=True)
            with redirect_stdout(StringIO()):
                TOOLS.import_chatgpt(args)
            self.assertFalse((root / "imports").exists())

    def test_import_rejects_zip_without_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("other.json", "[]")
            with self.assertRaisesRegex(ValueError, "conversations.json"):
                TOOLS._load_conversations(path)


if __name__ == "__main__":
    unittest.main()
