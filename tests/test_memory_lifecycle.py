import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_CLI = REPO_ROOT / "src" / "memory.py"
TOOLS_CLI = REPO_ROOT / "src" / "memory_tools.py"
DISTILL_CLI = REPO_ROOT / "src" / "memory_distill.py"


def load_memory_module():
    spec = importlib.util.spec_from_file_location("memory_module_for_lifecycle_tests", MEMORY_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_distill_module():
    spec = importlib.util.spec_from_file_location("memory_distill_module_for_lifecycle_tests", DISTILL_CLI)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DISTILL_CLI.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class LifecycleCommandTests(unittest.TestCase):
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

    def write_docx(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
            )

    def db_rows(self, db, sql, params=()):
        with sqlite3.connect(db) as conn:
            return conn.execute(sql, params).fetchall()

    def test_init_creates_recent_and_manual_import_manifest_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            first = self.run_cli(MEMORY_CLI, "init", "--root", str(root))
            manifest = root / "imports" / "manual" / "import_manifest.json"
            manifest.write_text('{"format_version":99,"keep":true}\n', encoding="utf-8")
            second = self.run_cli(MEMORY_CLI, "init", "--root", str(root))

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            for relative in [
                "memory/recent",
                "imports/manual/inbox",
                "imports/manual/raw",
                "imports/manual/quarantine",
                "imports/chatgpt/conversations",
            ]:
                self.assertTrue((root / relative).is_dir(), relative)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8")), {"format_version": 99, "keep": True})

    def test_v2_memory_recent_body_and_relations_are_validated_and_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            long_term = root / "memory" / "principles" / "principle-agent.md"
            long_term.write_text(
                """---
schema: "memory/v2"
id: "principle-agent"
type: "principle"
title: "Agent 原则"
created: "2026-06-01"
updated: "2026-06-01"
status: "active"
scope: "global"
workspace: "personal"
confidentiality: "personal"
source: "user"
confidence: "confirmed"
subject: "agent"
predicate: "prefers"
object: "deterministic-memory"
aliases:
  - "确定性记忆"
relations:
  - "supports:decision-no-gui"
source_refs:
  - "recent-manual-abc"
tags: []
content: |-
  Agent 记忆必须可确定。
---

# Agent 原则

正文证据链接到 [[decision-no-gui#背景]] 和 [[missing-target^b1]]。
""",
                encoding="utf-8",
            )
            recent = root / "memory" / "recent" / "recent-manual-abc.md"
            recent.write_text(
                """---
schema: "recent/v1"
id: "recent-manual-abc"
type: "session"
tier: "recent"
title: "手动证据"
created: "2026-05-01"
updated: "2026-05-01"
status: "active"
source: "manual_import"
source_id: "abc"
source_path: "imports/manual/raw/2026/05/abc-note.txt"
source_sha256: "abc"
retention_until: "2026-05-31"
distillation_status: "pending"
protected: "false"
content: |-
  近期正文完整保留。
---

近期正文完整保留。
""",
                encoding="utf-8",
            )

            validate = self.run_cli(MEMORY_CLI, "validate", "--root", str(root))
            index = self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir))
            rows = self.db_rows(state_dir / "memory.sqlite", "SELECT schema, tier, body, aliases FROM memories WHERE id = ?", ("principle-agent",))
            link_rows = self.db_rows(state_dir / "memory.sqlite", "SELECT source_id, target_ref, relation, resolved FROM links ORDER BY target_ref")

            self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)
            self.assertEqual(index.returncode, 0, index.stdout + index.stderr)
            self.assertEqual(rows[0][0], "memory/v2")
            self.assertEqual(rows[0][1], "long")
            self.assertIn("正文证据链接", rows[0][2])
            self.assertEqual(json.loads(rows[0][3]), ["确定性记忆"])
            self.assertIn(("principle-agent", "decision-no-gui", "supports", 0), link_rows)
            self.assertIn(("principle-agent", "missing-target", "related_to", 0), link_rows)

    def test_invalid_relation_fails_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            path = root / "memory" / "principles" / "principle-bad.md"
            path.write_text(
                """---
schema: "memory/v2"
id: "principle-bad"
type: "principle"
title: "Bad"
created: "2026-06-01"
updated: "2026-06-01"
status: "active"
scope: "global"
workspace: "personal"
confidentiality: "personal"
source: "user"
confidence: "confirmed"
subject: "a"
predicate: "b"
object: "c"
relations:
  - "owns:target"
tags: []
content: |-
  bad
---
bad
""",
                encoding="utf-8",
            )

            result = self.run_cli(MEMORY_CLI, "validate", "--root", str(root))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("relation has invalid predicate", result.stdout)

    def test_manual_import_archives_raw_generates_recent_indexes_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            inbox_file = root / "imports" / "manual" / "inbox" / "note.txt"
            inbox_file.write_text("手动导入正文\n", encoding="utf-8")

            first = self.run_cli(TOOLS_CLI, "import-manual", "--root", str(root), "--state-dir", str(state_dir), "--scan-inbox", "--json")
            second = self.run_cli(TOOLS_CLI, "import-manual", "--root", str(root), "--state-dir", str(state_dir), "--file", str(inbox_file), "--json")
            summary = json.loads(first.stdout)
            duplicate = json.loads(second.stdout)
            raw_files = sorted(path for path in (root / "imports" / "manual" / "raw").rglob("*") if path.is_file())
            recent_files = sorted((root / "memory" / "recent").glob("recent-manual-*.md"))
            indexed = self.db_rows(state_dir / "memory.sqlite", "SELECT id, tier, source_sha256 FROM memories WHERE id LIKE 'recent-manual-%'")

            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(summary["imported"], 1)
            self.assertEqual(duplicate["duplicates"], 1)
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(len(recent_files), 1)
            self.assertEqual(len(indexed), 1)
            self.assertEqual(indexed[0][2], hashlib.sha256(inbox_file.read_bytes()).hexdigest())

    def test_manual_import_docx_and_unsupported_raw_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            docx = Path(tmp) / "paper.docx"
            binary = Path(tmp) / "data.bin"
            self.write_docx(docx, "DOCX 正文")
            binary.write_bytes(b"\x00\x01")

            docx_result = self.run_cli(TOOLS_CLI, "import-manual", "--root", str(root), "--state-dir", str(state_dir), "--file", str(docx), "--json")
            binary_result = self.run_cli(TOOLS_CLI, "import-manual", "--root", str(root), "--state-dir", str(state_dir), "--file", str(binary), "--json")

            self.assertEqual(docx_result.returncode, 0, docx_result.stdout + docx_result.stderr)
            self.assertEqual(json.loads(docx_result.stdout)["imported"], 1)
            self.assertEqual(binary_result.returncode, 0, binary_result.stdout + binary_result.stderr)
            self.assertEqual(json.loads(binary_result.stdout)["archived_without_text"], 1)

    def test_recall_and_link_tools_return_relation_aware_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            self.run_cli(
                MEMORY_CLI,
                "add",
                "--root",
                str(root),
                "--type",
                "decision",
                "--title",
                "No GUI",
                "--scope",
                "global",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "不要创建 GUI。",
            )
            path = next((root / "memory" / "decisions").glob("*.md"))
            text = path.read_text(encoding="utf-8")
            memory_id = re.search(r'^id: "([^"]+)"$', text, re.MULTILINE).group(1)
            path.write_text(text.replace("tags: []", 'aliases:\n  - "界面禁令"\nrelations:\n  - "related_to:missing"\ntags: []'), encoding="utf-8")
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)

            recall = self.run_cli(TOOLS_CLI, "recall", "界面禁令", "--root", str(root), "--state-dir", str(state_dir), "--json")
            outgoing = self.run_cli(TOOLS_CLI, "outgoing", memory_id, "--root", str(root), "--state-dir", str(state_dir), "--json")
            unresolved = self.run_cli(TOOLS_CLI, "unresolved", "--root", str(root), "--state-dir", str(state_dir), "--json")

            self.assertEqual(recall.returncode, 0, recall.stdout + recall.stderr)
            self.assertEqual(json.loads(recall.stdout)["primary"][0]["id"], memory_id)
            self.assertEqual(json.loads(outgoing.stdout)["links"][0]["target_ref"], "missing")
            self.assertEqual(json.loads(unresolved.stdout)["unresolved"][0]["target_ref"], "missing")

    def test_launchd_plist_contains_watchpaths_and_scan_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)

            result = self.run_cli(TOOLS_CLI, "launchd-plist", "--root", str(root), "--state-dir", str(state_dir))

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("<key>WatchPaths</key>", result.stdout)
            self.assertIn(str(root / "imports" / "manual" / "inbox"), result.stdout)
            self.assertIn("import-manual", result.stdout)
            self.assertIn("--scan-inbox", result.stdout)


class DistillationLifecycleTests(unittest.TestCase):
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

    def add_recent_with_raw(self, root, days_old=30, protected="false"):
        raw = root / "imports" / "manual" / "raw" / "2026" / "05" / "abc-note.txt"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("raw evidence", encoding="utf-8")
        source_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
        updated = date.today() - timedelta(days=days_old)
        recent = root / "memory" / "recent" / "recent-manual-abc.md"
        recent.write_text(
            f"""---
schema: "recent/v1"
id: "recent-manual-abc"
type: "session"
tier: "recent"
title: "手动证据"
created: "{updated.isoformat()}"
updated: "{updated.isoformat()}"
status: "active"
source: "manual_import"
source_id: "abc"
source_path: "{raw.relative_to(root).as_posix()}"
source_sha256: "{source_sha}"
retention_until: "{(updated + timedelta(days=30)).isoformat()}"
distillation_status: "pending"
protected: "{protected}"
content: |-
  这是一条可蒸馏事实。
---

这是一条可蒸馏事实。
""",
            encoding="utf-8",
        )
        return recent, raw

    def write_fake_codex(self, path, result):
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib\n"
            "pathlib.Path('distillation_result.json').write_text("
            + repr(json.dumps(result, ensure_ascii=False))
            + ", encoding='utf-8')\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def write_fake_codex_text(self, path, text):
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib\n"
            "pathlib.Path('distillation_result.json').write_text("
            + repr(text)
            + ", encoding='utf-8')\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def test_run_uses_noninteractive_exec_for_real_codex_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            source_hash = "abc123"
            (task_dir / "task.json").write_text(
                json.dumps({"source_id": "recent-manual-abc", "source_sha256": source_hash}),
                encoding="utf-8",
            )
            (task_dir / "source.md").write_text("临时 recent 内容", encoding="utf-8")
            (task_dir / "existing_memories.json").write_text("[]", encoding="utf-8")
            (task_dir / "result_schema.json").write_text('{"schema":"distillation-result/v1"}', encoding="utf-8")
            fake = Path(tmp) / "codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                "if len(sys.argv) < 2 or sys.argv[1] != 'exec':\n"
                "    sys.exit(7)\n"
                "out = sys.argv[sys.argv.index('--output-last-message') + 1]\n"
                "task = json.loads(pathlib.Path('task.json').read_text(encoding='utf-8'))\n"
                "pathlib.Path(out).write_text(json.dumps({\n"
                "    'schema': 'distillation-result/v1',\n"
                "    'source_id': task['source_id'],\n"
                "    'source_sha256': task['source_sha256'],\n"
                "    'candidates': [],\n"
                "    'unresolved_conflicts': []\n"
                "}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)

            run = self.run_cli(DISTILL_CLI, "run", "--task-dir", str(task_dir), "--codex-bin", str(fake), "--timeout", "5", "--json")

            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            argv = json.loads((task_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertEqual(argv[0], "exec")
            self.assertIn("--skip-git-repo-check", argv)
            self.assertIn("--ephemeral", argv)
            self.assertIn("--output-last-message", argv)

    def test_run_timeout_writes_text_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            (task_dir / "task.json").write_text("{}", encoding="utf-8")
            fake = Path(tmp) / "fake_timeout.py"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import sys, time\n"
                "sys.stderr.write('starting slow codex\\n')\n"
                "sys.stderr.flush()\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            module = load_distill_module()

            with self.assertRaisesRegex(ValueError, "timed out"):
                module.run_task(type("Args", (), {"task_dir": str(task_dir), "codex_bin": str(fake), "timeout": 1})())

            self.assertEqual((task_dir / "codex_exit_code.txt").read_text(encoding="utf-8"), "124\n")
            self.assertIn("starting slow codex", (task_dir / "codex_stderr.log").read_text(encoding="utf-8"))

    def test_prepare_run_apply_and_purge_keep_raw_and_delete_only_safe_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)
            source_hash = hashlib.sha256(recent.read_bytes()).hexdigest()
            fake = Path(tmp) / "fake_codex.py"
            self.write_fake_codex(
                fake,
                {
                    "schema": "distillation-result/v1",
                    "source_id": "recent-manual-abc",
                    "source_sha256": source_hash,
                    "candidates": [
                        {
                            "action": "create",
                            "type": "principle",
                            "title": "蒸馏事实",
                            "subject": "agent",
                            "predicate": "remembers",
                            "object": "facts",
                            "content": "这是一条可蒸馏事实。",
                            "evidence": "这是一条可蒸馏事实",
                        }
                    ],
                    "unresolved_conflicts": [],
                },
            )

            prepare = self.run_cli(DISTILL_CLI, "prepare", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")
            task = json.loads(prepare.stdout)["tasks"][0]
            run = self.run_cli(DISTILL_CLI, "run", "--task-dir", task["task_dir"], "--codex-bin", str(fake), "--timeout", "5", "--json")
            apply = self.run_cli(DISTILL_CLI, "apply", "--root", str(root), "--state-dir", str(state_dir), "--task-dir", task["task_dir"], "--today", date.today().isoformat(), "--json")
            early = self.run_cli(DISTILL_CLI, "purge", "--root", str(root), "--state-dir", str(state_dir), "--today", (date.today() + timedelta(days=6)).isoformat(), "--json")

            self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
            self.assertEqual(json.loads(apply.stdout)["applied"], 1)
            self.assertEqual(json.loads(early.stdout)["deleted"], 0)
            self.assertTrue(recent.exists())
            late = self.run_cli(DISTILL_CLI, "purge", "--root", str(root), "--state-dir", str(state_dir), "--today", (date.today() + timedelta(days=7)).isoformat(), "--json")
            self.assertEqual(late.returncode, 0, late.stdout + late.stderr)
            self.assertEqual(json.loads(late.stdout)["deleted"], 1)
            self.assertFalse(recent.exists())
            self.assertTrue(raw.exists())

    def test_purge_refuses_when_recent_hash_changed_or_raw_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            module = load_memory_module()
            module.index_store(type("Args", (), {"root": str(root), "state_dir": str(state_dir), "dry_run": False})())
            with sqlite3.connect(state_dir / "memory.sqlite") as conn:
                conn.execute(
                    """
                    INSERT INTO distillation_audit(recent_id, relative_path, source_sha256, status, result_json, distilled_at, delete_after)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "recent-manual-abc",
                        recent.relative_to(root).as_posix(),
                        hashlib.sha256(recent.read_bytes()).hexdigest(),
                        "pending_delete",
                        "{}",
                        date.today().isoformat(),
                        date.today().isoformat(),
                    ),
                )
                conn.commit()
            recent.write_text(recent.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            changed = self.run_cli(DISTILL_CLI, "purge", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")
            recent.write_text(recent.read_text(encoding="utf-8").replace("\nchanged\n", ""), encoding="utf-8")
            raw.unlink()
            missing_raw = self.run_cli(DISTILL_CLI, "purge", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")

            self.assertEqual(changed.returncode, 0, changed.stdout + changed.stderr)
            self.assertEqual(json.loads(changed.stdout)["deleted"], 0)
            self.assertTrue(recent.exists())
            self.assertEqual(missing_raw.returncode, 0, missing_raw.stdout + missing_raw.stderr)
            self.assertEqual(json.loads(missing_raw.stdout)["deleted"], 0)
            self.assertTrue(recent.exists())

    def test_purge_refuses_when_raw_hash_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            module = load_memory_module()
            module.index_store(type("Args", (), {"root": str(root), "state_dir": str(state_dir), "dry_run": False})())
            with sqlite3.connect(state_dir / "memory.sqlite") as conn:
                conn.execute(
                    """
                    INSERT INTO distillation_audit(recent_id, relative_path, source_sha256, status, result_json, distilled_at, delete_after)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "recent-manual-abc",
                        recent.relative_to(root).as_posix(),
                        hashlib.sha256(recent.read_bytes()).hexdigest(),
                        "pending_delete",
                        "{}",
                        date.today().isoformat(),
                        date.today().isoformat(),
                    ),
                )
                conn.commit()
            raw.write_text("changed raw evidence", encoding="utf-8")

            changed_raw = self.run_cli(DISTILL_CLI, "purge", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")

            self.assertEqual(changed_raw.returncode, 0, changed_raw.stdout + changed_raw.stderr)
            self.assertEqual(json.loads(changed_raw.stdout)["deleted"], 0)
            self.assertTrue(recent.exists())
            self.assertTrue(raw.exists())

    def test_purge_dry_run_reports_without_deleting_recent_or_updating_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            module = load_memory_module()
            module.index_store(type("Args", (), {"root": str(root), "state_dir": str(state_dir), "dry_run": False})())
            with sqlite3.connect(state_dir / "memory.sqlite") as conn:
                conn.execute(
                    """
                    INSERT INTO distillation_audit(recent_id, relative_path, source_sha256, status, result_json, distilled_at, delete_after)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "recent-manual-abc",
                        recent.relative_to(root).as_posix(),
                        hashlib.sha256(recent.read_bytes()).hexdigest(),
                        "pending_delete",
                        '{"schema":"distillation-result/v1","unresolved_conflicts":[]}',
                        date.today().isoformat(),
                        date.today().isoformat(),
                    ),
                )
                conn.commit()

            dry_run = self.run_cli(
                DISTILL_CLI,
                "purge",
                "--root",
                str(root),
                "--state-dir",
                str(state_dir),
                "--today",
                date.today().isoformat(),
                "--dry-run",
                "--json",
            )

            self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
            summary = json.loads(dry_run.stdout)
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["deleted"], 0)
            self.assertEqual(summary["would_delete"], 1)
            self.assertTrue(recent.exists())
            self.assertTrue(raw.exists())
            with sqlite3.connect(state_dir / "memory.sqlite") as conn:
                status = conn.execute(
                    "SELECT status FROM distillation_audit WHERE recent_id = ?",
                    ("recent-manual-abc",),
                ).fetchone()[0]
            self.assertEqual(status, "pending_delete")

    def test_distillation_failure_paths_do_not_write_or_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)
            prepare = self.run_cli(DISTILL_CLI, "prepare", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")
            task = json.loads(prepare.stdout)["tasks"][0]
            fake = Path(tmp) / "fake_bad_json.py"
            self.write_fake_codex_text(fake, "{not-json")

            run = self.run_cli(DISTILL_CLI, "run", "--task-dir", task["task_dir"], "--codex-bin", str(fake), "--timeout", "5", "--json")

            self.assertNotEqual(run.returncode, 0)
            self.assertTrue(recent.exists())
            self.assertFalse(list((root / "memory" / "principles").glob("*.md")))
            self.assertTrue(raw.exists())

    def test_apply_rejects_wrong_hash_and_unknown_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)
            prepare = self.run_cli(DISTILL_CLI, "prepare", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")
            task = json.loads(prepare.stdout)["tasks"][0]
            result_path = Path(task["task_dir"]) / "distillation_result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema": "distillation-result/v1",
                        "source_id": "recent-manual-abc",
                        "source_sha256": "wrong",
                        "candidates": [{"action": "create"}],
                        "unresolved_conflicts": [],
                    }
                ),
                encoding="utf-8",
            )

            wrong_hash = self.run_cli(DISTILL_CLI, "apply", "--root", str(root), "--state-dir", str(state_dir), "--task-dir", task["task_dir"], "--json")
            task_json = json.loads((Path(task["task_dir"]) / "task.json").read_text(encoding="utf-8"))
            result_path.write_text(
                json.dumps(
                    {
                        "schema": "distillation-result/v1",
                        "source_id": "recent-manual-abc",
                        "source_sha256": task_json["source_sha256"],
                        "candidates": [{"action": "invent"}],
                        "unresolved_conflicts": [],
                    }
                ),
                encoding="utf-8",
            )
            unknown_action = self.run_cli(DISTILL_CLI, "apply", "--root", str(root), "--state-dir", str(state_dir), "--task-dir", task["task_dir"], "--json")

            self.assertNotEqual(wrong_hash.returncode, 0)
            self.assertNotEqual(unknown_action.returncode, 0)
            self.assertTrue(recent.exists())
            self.assertFalse(list((root / "memory" / "principles").glob("*.md")))
            self.assertTrue(raw.exists())

    def test_prepare_skips_protected_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            self.add_recent_with_raw(root, protected="true")
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)

            prepare = self.run_cli(DISTILL_CLI, "prepare", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")

            self.assertEqual(prepare.returncode, 0, prepare.stdout + prepare.stderr)
            self.assertEqual(json.loads(prepare.stdout)["count"], 0)

    def test_apply_support_and_supersede_update_existing_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state_dir = Path(tmp) / "state"
            self.init_store(root, state_dir)
            recent, raw = self.add_recent_with_raw(root)
            target = self.run_cli(
                MEMORY_CLI,
                "add",
                "--root",
                str(root),
                "--type",
                "principle",
                "--title",
                "旧原则",
                "--scope",
                "global",
                "--workspace",
                "personal",
                "--confidentiality",
                "personal",
                "--source",
                "user",
                "--confidence",
                "confirmed",
                "--content",
                "旧内容。",
            )
            self.assertEqual(target.returncode, 0, target.stdout + target.stderr)
            target_path = next((root / "memory" / "principles").glob("*.md"))
            target_id = re.search(r'^id: "([^"]+)"$', target_path.read_text(encoding="utf-8"), re.MULTILINE).group(1)
            self.assertEqual(self.run_cli(MEMORY_CLI, "index", "--root", str(root), "--state-dir", str(state_dir)).returncode, 0)
            prepare = self.run_cli(DISTILL_CLI, "prepare", "--root", str(root), "--state-dir", str(state_dir), "--today", date.today().isoformat(), "--json")
            task = json.loads(prepare.stdout)["tasks"][0]
            task_json = json.loads((Path(task["task_dir"]) / "task.json").read_text(encoding="utf-8"))
            (Path(task["task_dir"]) / "distillation_result.json").write_text(
                json.dumps(
                    {
                        "schema": "distillation-result/v1",
                        "source_id": "recent-manual-abc",
                        "source_sha256": task_json["source_sha256"],
                        "candidates": [
                            {"action": "support", "target_id": target_id},
                            {
                                "action": "supersede",
                                "target_id": target_id,
                                "type": "principle",
                                "title": "新原则",
                                "subject": "agent",
                                "predicate": "uses",
                                "object": "new-rule",
                                "content": "新内容。",
                            },
                        ],
                        "unresolved_conflicts": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            applied = self.run_cli(DISTILL_CLI, "apply", "--root", str(root), "--state-dir", str(state_dir), "--task-dir", task["task_dir"], "--json")
            old_text = target_path.read_text(encoding="utf-8")
            new_text = next(path for path in (root / "memory" / "principles").glob("*.md") if path != target_path).read_text(encoding="utf-8")

            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertIn('status: "historical"', old_text)
            self.assertIn("source_refs:", old_text)
            self.assertIn("supports:recent-manual-abc", old_text)
            self.assertIn("superseded_by:", old_text)
            self.assertIn("supersedes:" + target_id, new_text)


if __name__ == "__main__":
    unittest.main()
