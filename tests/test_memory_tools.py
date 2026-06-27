import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))
import memory as MEMORY
SPEC = importlib.util.spec_from_file_location("memory_tools", SRC / "memory_tools.py")
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class MemoryToolsTests(unittest.TestCase):
    def make_root(self, base):
        root = base / "data"
        MEMORY.init_store(root)
        return root

    def make_database(self, state, root):
        state.mkdir()
        db = state / "memory.sqlite"
        path = root / "memory/principles/principle-1-probe.md"
        MEMORY.atomic_write_text(
            path,
            MEMORY.render_memory({
                "id": "principle-1", "type": "principle", "title": "代码最少原则",
                "content": "使用尽可能少的代码实现相同功能。", "tags": ["coding"],
                "status": "active", "scope": "global", "workspace": "personal",
                "confidentiality": "personal", "source": "user", "confidence": "confirmed",
                "created": "2026-06-22", "updated": "2026-06-22",
            }),
        )
        MEMORY.create_database(db)
        MEMORY.index_store(argparse.Namespace(root=str(root), state_dir=str(state), dry_run=False))
        return db

    def test_search_does_not_reimplement_query_parser(self):
        self.assertNotIn("_fts_query", TOOLS.__dict__)

    def test_search_returns_chinese_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            state = base / "state"
            self.make_database(state, root)
            args = argparse.Namespace(
                root=str(root), state_dir=str(state), query="代码最少", type=None,
                project=None, workspace=None, status=None, limit=20, json=True,
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(TOOLS.search(args), 1)
            self.assertEqual(json.loads(output.getvalue())[0]["id"], "principle-1")

    def test_search_delegates_to_canonical_search_store(self):
        args = argparse.Namespace(
            root="/tmp/root", state_dir="/tmp/state", query="probe", type=None,
            project=None, workspace=None, status=None, limit=20, json=True,
            include_restricted=False, include_inactive=False,
        )
        with mock.patch.object(TOOLS, "search_store", return_value=[]) as delegated:
            with redirect_stdout(StringIO()):
                TOOLS.search(args)
        delegated.assert_called_once()
        self.assertEqual(delegated.call_args.args[0].kind, "memory")

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
            original = next((root / "imports/chatgpt/conversations").rglob("*.md"))
            original_hash = original.read_bytes()
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
            self.assertEqual(len(files), 2)
            self.assertEqual(original.read_bytes(), original_hash)
            changed_text = [path.read_text(encoding="utf-8") for path in files if path != original][0]
            self.assertIn("# 重命名对话", changed_text)
            self.assertIn("## User", changed_text)
            self.assertIn("## Assistant", changed_text)
            reports = list((root / "exports/import_reports").glob("*.json"))
            self.assertTrue(reports)
            report = json.loads(reports[-1].read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "chatgpt")
            self.assertEqual(report["zip_valid"], True)
            self.assertEqual(report["failed"], 0)
            manifest = json.loads((root / "imports/chatgpt/import_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["conversations"]), 2)

    def test_import_chatgpt_collision_path_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), zip=str(export), dry_run=False)
            with redirect_stdout(StringIO()):
                TOOLS.import_chatgpt(args)
            original = next((root / "imports/chatgpt/conversations").rglob("*.md"))

            self.sample_export(export, title="changed title")
            conversation = TOOLS.preflight_chatgpt_export(export)["conversations"][0]
            content, _ = TOOLS._render_conversation(conversation)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            collision = original.with_name(f"{original.stem}-{digest[:12]}{original.suffix}")
            collision.write_bytes(b"pre-existing collision")
            os.utime(collision, (1_700_000_000, 1_700_000_000))
            collision_bytes = collision.read_bytes()
            collision_mtime = collision.stat().st_mtime_ns

            with redirect_stdout(StringIO()):
                report = TOOLS.import_chatgpt(args)

            self.assertEqual(collision.read_bytes(), collision_bytes)
            self.assertEqual(collision.stat().st_mtime_ns, collision_mtime)
            manifest = json.loads((root / "imports/chatgpt/import_manifest.json").read_text(encoding="utf-8"))
            entry = next(record for record in manifest["conversations"] if record["sha256"] == digest)
            actual = root / entry["path"]
            self.assertNotEqual(actual, collision)
            self.assertEqual(hashlib.sha256(actual.read_bytes()).hexdigest(), digest)
            self.assertIn(entry["path"], report["written_paths"])
            with redirect_stdout(StringIO()):
                repeated = TOOLS.import_chatgpt(args)
            self.assertEqual(repeated["unchanged"], 1)
            self.assertEqual(collision.read_bytes(), collision_bytes)

    def test_import_chatgpt_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), zip=str(export), dry_run=True)
            with redirect_stdout(StringIO()):
                TOOLS.import_chatgpt(args)
            self.assertFalse(list((root / "imports/chatgpt").rglob("*.md")))
            self.assertFalse(list((root / "exports/import_reports").glob("*.json")))

    def test_import_rejects_zip_without_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("other.json", "[]")
            with self.assertRaisesRegex(ValueError, "conversations.json"):
                TOOLS._load_conversations(path)

    def test_import_rejects_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_text("not a zip", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid ChatGPT export ZIP"):
                TOOLS._load_conversations(path)

    def test_import_rejects_non_list_conversations_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("conversations.json", "{}")
            with self.assertRaisesRegex(ValueError, "must contain a list"):
                TOOLS._load_conversations(path)

    def test_import_rejects_multiple_conversations_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("a/conversations.json", "[]")
                archive.writestr("b/conversations.json", "[]")
            with self.assertRaisesRegex(ValueError, "Multiple conversations.json"):
                TOOLS._load_conversations(path)

    def test_import_preflight_counts_invalid_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("conversations.json", json.dumps([{"id": "ok", "mapping": {}}, {"bad": True}]))
            report = TOOLS.preflight_chatgpt_export(path)
            self.assertEqual(report["conversation_count"], 2)
            self.assertEqual(report["invalid_conversations"], 2)
            self.assertEqual(report["conversations"], [])

    def test_import_chatgpt_reports_raw_only_without_recent_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            export = base / "export.zip"
            self.sample_export(export)
            args = argparse.Namespace(root=str(root), zip=str(export), dry_run=False)
            with redirect_stdout(StringIO()):
                TOOLS.import_chatgpt(args)
            with redirect_stdout(StringIO()):
                report = TOOLS.import_chatgpt(args)
            self.assertEqual(report["raw_only"], 1)
            self.assertIn("No recent restore", report["restore_suggestion"])
            parser = TOOLS.build_parser()
            with redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["import-chatgpt", "--zip", "x.zip", "--root", "root", "--restore-recent"])

    def test_import_manual_writes_raw_text_sidecar_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            source = base / "note.txt"
            source.write_text("Manual PDC sidecar text", encoding="utf-8")
            args = argparse.Namespace(root=str(root), path=str(source), dry_run=False)

            with redirect_stdout(StringIO()):
                report = TOOLS.import_manual(args)

            raw = root / report["written_paths"][0]
            text = root / report["written_paths"][1]
            self.assertTrue(raw.is_file())
            self.assertTrue(text.is_file())
            text_content = text.read_text(encoding="utf-8")
            self.assertIn("Manual PDC sidecar text", text_content)
            self.assertIn(f'source_path: "{report["written_paths"][0]}"', text_content)
            self.assertIn('original_name: "note.txt"', text_content)
            self.assertEqual(report["archived_without_text"], 0)
            self.assertTrue(list((root / "exports/import_reports").glob("*.json")))

    def test_import_manual_versions_same_name_without_overwriting_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            source = base / "note.txt"
            source.write_text("first version", encoding="utf-8")
            args = argparse.Namespace(root=str(root), path=str(source), dry_run=False)
            with redirect_stdout(StringIO()):
                first = TOOLS.import_manual(args)
            old_raw = root / first["written_paths"][0]
            old_bytes = old_raw.read_bytes()

            source.write_text("second version", encoding="utf-8")
            with redirect_stdout(StringIO()):
                second = TOOLS.import_manual(args)

            self.assertNotEqual(first["written_paths"][0], second["written_paths"][0])
            self.assertEqual(old_raw.read_bytes(), old_bytes)
            self.assertEqual(len(list((root / "imports/manual/raw").rglob("*.txt"))), 2)

    def test_import_manual_collision_path_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            source = base / "note.txt"
            source.write_bytes(b"manual collision payload")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            now = datetime.now(timezone.utc)
            collision = root / f"imports/manual/raw/{now.year:04d}/{now.month:02d}/{digest[:16]}-note.txt"
            collision.parent.mkdir(parents=True, exist_ok=True)
            collision.write_bytes(b"pre-existing manual collision")
            os.utime(collision, (1_700_000_000, 1_700_000_000))
            collision_bytes = collision.read_bytes()
            collision_mtime = collision.stat().st_mtime_ns
            args = argparse.Namespace(root=str(root), path=str(source), dry_run=False)

            with redirect_stdout(StringIO()):
                first = TOOLS.import_manual(args)

            actual = root / first["written_paths"][0]
            self.assertNotEqual(actual, collision)
            self.assertEqual(actual.read_bytes(), source.read_bytes())
            self.assertEqual(collision.read_bytes(), collision_bytes)
            self.assertEqual(collision.stat().st_mtime_ns, collision_mtime)
            with redirect_stdout(StringIO()):
                second = TOOLS.import_manual(args)
            self.assertEqual(second["written_paths"][0], first["written_paths"][0])
            self.assertEqual(second["duplicate"], 1)

    def test_exclusive_raw_open_failure_never_deletes_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "raw.txt"
            target.write_bytes(b"existing raw")

            with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    TOOLS._write_versioned_bytes(target, b"new raw")

            self.assertEqual(target.read_bytes(), b"existing raw")

    def test_import_manual_archives_binary_without_text_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.make_root(base)
            source = base / "paper.pdf"
            source.write_bytes(b"%PDF-\xff\x00not utf8")
            args = argparse.Namespace(root=str(root), path=str(source), dry_run=False)

            with redirect_stdout(StringIO()):
                report = TOOLS.import_manual(args)

            self.assertEqual(report["archived_without_text"], 1)
            self.assertEqual(len(report["written_paths"]), 1)
            self.assertTrue((root / report["written_paths"][0]).is_file())
            self.assertFalse(list((root / "imports/manual/text").glob("*")))
            saved = json.loads(list((root / "exports/import_reports").glob("*.json"))[0].read_text(encoding="utf-8"))
            self.assertEqual(saved["archived_without_text"], 1)

    def test_serve_chatgpt_command_is_removed(self):
        parser = TOOLS.build_parser()
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["serve-" + "chatgpt", "--root", "x"])


if __name__ == "__main__":
    unittest.main()
