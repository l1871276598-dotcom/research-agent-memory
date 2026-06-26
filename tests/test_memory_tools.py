import argparse
import importlib.util
import json
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
MEMORY_SPEC = importlib.util.spec_from_file_location("memory", SRC / "memory.py")
MEMORY = importlib.util.module_from_spec(MEMORY_SPEC)
MEMORY_SPEC.loader.exec_module(MEMORY)
SPEC = importlib.util.spec_from_file_location("memory_tools", SRC / "memory_tools.py")
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class MemoryToolsTests(unittest.TestCase):
    def make_root(self, base):
        root = base / "data"
        MEMORY.init_store(root)
        return root

    def make_database(self, root, state):
        MEMORY.add_memory(
            argparse.Namespace(
                root=str(root),
                type="principle",
                title="代码最少原则",
                status="active",
                scope="global",
                workspace="personal",
                confidentiality="personal",
                source="user",
                confidence="confirmed",
                content="使用尽可能少的代码实现相同功能。",
                tags=["coding"],
                context_id=None,
                project=None,
                valid_from=None,
                valid_until=None,
                from_context=None,
                to_context=None,
                effective_date=None,
                reason=None,
            )
        )
        MEMORY.db_init(argparse.Namespace(root=str(root), state_dir=str(state)))
        MEMORY.index_store(argparse.Namespace(root=str(root), state_dir=str(state), dry_run=False))
        return state / "memory.sqlite"

    def add_memory(self, root, title, workspace="personal", confidentiality="personal", relations=None):
        memory_id, _path, _type, _status = MEMORY.add_memory(
            argparse.Namespace(
                root=str(root),
                type="principle",
                title=title,
                status="active",
                scope="global",
                workspace=workspace,
                confidentiality=confidentiality,
                source="user",
                confidence="confirmed",
                content=f"{title} content",
                tags=[],
                context_id=None,
                project=None,
                valid_from=None,
                valid_until=None,
                from_context=None,
                to_context=None,
                effective_date=None,
                reason=None,
            )
        )
        if relations:
            path = next((root / "memory" / "principles").glob(f"{memory_id}-*.md"))
            text = path.read_text(encoding="utf-8")
            relation_text = "\n".join(f'  - "{value}"' for value in relations)
            path.write_text(text.replace("tags: []", f"relations:\n{relation_text}\ntags: []"), encoding="utf-8")
        return memory_id

    def test_chinese_partial_query_uses_bigrams(self):
        self.assertEqual(TOOLS._fts_query("代码最少"), '"代码" AND "码最" AND "最少"')

    def test_search_returns_chinese_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            state = base / "state"
            self.make_database(root, state)
            args = argparse.Namespace(
                root=str(root), state_dir=str(state), query="代码最少", type=None,
                project=None, workspace=None, status=None, limit=20, json=True,
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(TOOLS.search(args), 1)
            row = json.loads(output.getvalue())[0]
            self.assertEqual(row["title"], "代码最少原则")
            self.assertTrue(row["id"].startswith("principle-"))

    def test_recall_does_not_return_links_to_restricted_memories_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            state = base / "state"
            restricted_id = self.add_memory(root, "受限原则", workspace="work", confidentiality="restricted")
            allowed_id = self.add_memory(root, "公开原则", relations=[f"related_to:{restricted_id}"])
            MEMORY.db_init(argparse.Namespace(root=str(root), state_dir=str(state)))
            MEMORY.index_store(argparse.Namespace(root=str(root), state_dir=str(state), dry_run=False))

            summary = TOOLS.recall(argparse.Namespace(root=str(root), state_dir=str(state), query="公开原则", workspace="personal"))

            self.assertEqual([row["id"] for row in summary["primary"]], [allowed_id])
            self.assertEqual(summary["related"], [])

    def test_relation_queries_do_not_return_restricted_sources_or_targets_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            state = base / "state"
            restricted_id = self.add_memory(root, "受限原则", workspace="work", confidentiality="restricted")
            allowed_id = self.add_memory(root, "公开原则", relations=[f"related_to:{restricted_id}"])
            MEMORY.db_init(argparse.Namespace(root=str(root), state_dir=str(state)))
            MEMORY.index_store(argparse.Namespace(root=str(root), state_dir=str(state), dry_run=False))

            outgoing = TOOLS.link_query(argparse.Namespace(root=str(root), state_dir=str(state), target_id=allowed_id, workspace="personal"), "outgoing")
            backlinks = TOOLS.link_query(argparse.Namespace(root=str(root), state_dir=str(state), target_id=restricted_id, workspace="personal"), "backlinks")
            related = TOOLS.link_query(argparse.Namespace(root=str(root), state_dir=str(state), target_id="", workspace="personal"), "related")

            self.assertEqual(outgoing["links"], [])
            self.assertEqual(backlinks["links"], [])
            self.assertEqual(related["links"], [])

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
            state = base / "state"
            MEMORY.db_init(argparse.Namespace(root=str(root), state_dir=str(state)))
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), state_dir=str(state), zip=str(export), dry_run=False)
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
            state = base / "state"
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), state_dir=str(state), zip=str(export), dry_run=True)
            with redirect_stdout(StringIO()):
                TOOLS.import_chatgpt(args)
            self.assertFalse(list((root / "imports" / "chatgpt" / "conversations").rglob("*.md")))
            self.assertFalse(list((root / "memory" / "recent").glob("recent-chatgpt-*.md")))

    def test_import_rejects_zip_without_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("other.json", "[]")
            with self.assertRaisesRegex(ValueError, "conversations.json"):
                TOOLS._load_conversations(path)


if __name__ == "__main__":
    unittest.main()
