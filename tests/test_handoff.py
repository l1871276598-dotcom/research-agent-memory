import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


def write_project(root, project="research-agent-memory"):
    import memory

    record = {
        "id": f"project-{project}",
        "type": "project",
        "title": project,
        "created": "2026-07-06",
        "updated": "2026-07-06",
        "status": "active",
        "scope": "project",
        "workspace": "personal",
        "confidentiality": "personal",
        "source": "test",
        "confidence": "confirmed",
        "project": project,
        "content": f"Project {project}",
        "tags": [],
    }
    path = root / memory.TYPE_DIRS["project"] / f"{record['id']}-test.md"
    path.write_text(memory.render_memory(record), encoding="utf-8")
    return path


class HandoffUpdateTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        memory.init_store(self.root)
        write_project(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_project_scoped_handoff_without_touching_legacy_root(self):
        from handoff import update_project_handoff

        result = update_project_handoff(
            self.root,
            "research-agent-memory",
            "# Handoff\n\nproject state",
            workspace="personal",
        )

        target = self.root / "projects/research-agent-memory/handoff.md"
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["path"], "projects/research-agent-memory/handoff.md")
        self.assertTrue(target.is_file())
        self.assertFalse((self.root / "LAOS_HANDOFF.md").exists())
        text = target.read_text(encoding="utf-8")
        self.assertIn('project_slug: "research-agent-memory"', text)
        self.assertIn("# Handoff", text)
        self.assertFalse((self.root / "_staging/research-agent-memory/handoff.md").exists())

    def test_updates_only_matching_existing_handoff(self):
        from handoff import update_project_handoff

        created = update_project_handoff(self.root, "research-agent-memory", "first", workspace="personal")
        target = self.root / created["path"]
        before = hashlib.sha256(target.read_bytes()).hexdigest()

        updated = update_project_handoff(
            self.root,
            "research-agent-memory",
            "second",
            expected_sha256=before,
            workspace="personal",
        )

        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["previous_sha256"], before)
        self.assertIn("second", target.read_text(encoding="utf-8"))

    def test_rejects_missing_source_backed_project_without_writing(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "project_slug_not_source_backed"):
            update_project_handoff(self.root, "unknown-project", "content", workspace="personal")

        self.assertFalse((self.root / "projects/unknown-project/handoff.md").exists())

    def test_rejects_other_project_handoff_without_overwrite(self):
        from handoff import update_project_handoff

        target = self.root / "projects/research-agent-memory/handoff.md"
        target.parent.mkdir(parents=True)
        target.write_text('---\nproject_slug: "other-project"\n---\n\nold\n', encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "target_handoff_belongs_to_another_project"):
            update_project_handoff(self.root, "research-agent-memory", "new", workspace="personal")

        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_rejects_sha_mismatch_without_overwrite(self):
        from handoff import update_project_handoff

        update_project_handoff(self.root, "research-agent-memory", "first", workspace="personal")
        target = self.root / "projects/research-agent-memory/handoff.md"
        before = target.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            update_project_handoff(self.root, "research-agent-memory", "second", expected_sha256="0" * 64, workspace="personal")

        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_rejects_cross_workspace_project_without_writing(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "project_not_in_workspace"):
            update_project_handoff(
                self.root, "research-agent-memory", "content", workspace="work"
            )

        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

    def test_accepts_matching_workspace_explicitly(self):
        from handoff import update_project_handoff

        result = update_project_handoff(
            self.root, "research-agent-memory", "content", workspace="personal"
        )
        self.assertEqual(result["status"], "created")

    def test_rejects_invalid_workspace_value(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "workspace must be personal or work"):
            update_project_handoff(
                self.root, "research-agent-memory", "content", workspace="internal"
            )

    def test_agent_enforces_task_workspace(self):
        from agents.handoff import HandoffAgent

        agent = HandoffAgent(self.root)
        task = {
            "type": "handoff.write",
            "workspace": "work",
            "input": {"project_slug": "research-agent-memory", "content": "content"},
        }
        with self.assertRaisesRegex(ValueError, "project_not_in_workspace"):
            agent.run(task, {})
        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

        task["workspace"] = "personal"
        result = agent.run(task, {})
        self.assertEqual(result["output"]["status"], "created")

    def test_agent_requires_workspace(self):
        from agents.handoff import HandoffAgent

        agent = HandoffAgent(self.root)
        task = {
            "type": "handoff.write",
            "input": {"project_slug": "research-agent-memory", "content": "content"},
        }
        with self.assertRaises(ValueError):
            agent.run(task, {})

    def test_core_requires_explicit_workspace(self):
        from handoff import update_project_handoff

        with self.assertRaises(TypeError):
            update_project_handoff(self.root, "research-agent-memory", "content")

    def _facade(self):
        from tool_facade import LaosToolFacade
        from agents.handoff import HandoffAgent

        agent = HandoffAgent(self.root)

        class _MiniApplication:
            def run(self, task):
                return agent.run(task, {})

        return LaosToolFacade(_MiniApplication())

    def test_facade_end_to_end_rejects_cross_workspace(self):
        facade = self._facade()
        with self.assertRaisesRegex(ValueError, "project_not_in_workspace"):
            facade.handoff_write("research-agent-memory", "content", workspace="work")
        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

    def test_facade_end_to_end_requires_workspace(self):
        facade = self._facade()
        with self.assertRaises(TypeError):
            facade.handoff_write("research-agent-memory", "content")
        with self.assertRaisesRegex(ValueError, "workspace must be personal or work"):
            facade.handoff_write("research-agent-memory", "content", workspace="internal")

    def test_facade_end_to_end_matching_workspace_writes(self):
        facade = self._facade()
        result = facade.handoff_write("research-agent-memory", "content", workspace="personal")
        self.assertEqual(result["status"], "created")
        self.assertTrue(
            (self.root / "projects/research-agent-memory/handoff.md").is_file()
        )

    def test_facade_expected_sha256_stays_optional(self):
        facade = self._facade()
        result = facade.handoff_write(
            "research-agent-memory", "content", workspace="personal"
        )
        self.assertEqual(result["status"], "created")
        updated = facade.handoff_write(
            "research-agent-memory",
            "again",
            workspace="personal",
            expected_sha256=result["sha256"],
        )
        self.assertEqual(updated["status"], "updated")

    def test_existing_handoff_requires_expected_sha256(self):
        from handoff import update_project_handoff

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
        )
        target = self.root / created["path"]
        before = target.read_bytes()

        with self.assertRaisesRegex(ValueError, "expected_sha256 is required"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "second",
                workspace="personal",
            )

        self.assertEqual(target.read_bytes(), before)

    def test_receipt_binds_before_after_and_recomputes(self):
        from handoff import update_project_handoff

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
        )
        self.assertIsNone(created["before_sha256"])
        self.assertEqual(created["after_sha256"], created["sha256"])
        body = dict(created)
        digest = body.pop("receipt_sha256")
        canonical = json.dumps(
            body,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(digest, hashlib.sha256(canonical).hexdigest())

        updated = update_project_handoff(
            self.root,
            "research-agent-memory",
            "second",
            expected_sha256=created["sha256"],
            workspace="personal",
        )
        self.assertEqual(updated["before_sha256"], created["sha256"])
        self.assertEqual(updated["previous_sha256"], created["sha256"])
        self.assertEqual(updated["after_sha256"], updated["sha256"])
        self.assertEqual(updated["expected_sha256"], created["sha256"])

    def test_same_expected_sha256_allows_only_one_concurrent_update(self):
        from handoff import update_project_handoff

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
        )
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(SOURCE_DIR)!r});"
            "from handoff import update_project_handoff;"
            "root,content,expected=sys.argv[1:4];"
            "\ntry:\n"
            " r=update_project_handoff(root,'research-agent-memory',content,expected,workspace='personal');"
            " print(json.dumps({'ok':True,'result':r},sort_keys=True))"
            "\nexcept Exception as e:\n"
            " print(json.dumps({'ok':False,'error':str(e)},sort_keys=True))"
        )
        commands = [
            [
                sys.executable,
                "-c",
                script,
                str(self.root),
                content,
                created["sha256"],
            ]
            for content in ("second-a", "second-b")
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            results.append(json.loads(stdout))

        winners = [item for item in results if item["ok"]]
        losers = [item for item in results if not item["ok"]]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(losers), 1, results)
        self.assertIn("sha256 mismatch", losers[0]["error"])
        target = self.root / "projects/research-agent-memory/handoff.md"
        final_text = target.read_text(encoding="utf-8")
        winner_content = "second-a" if "second-a" in final_text else "second-b"
        self.assertIn(winner_content, final_text)
        self.assertFalse(list(target.parent.glob(".handoff-*.tmp")))
        self.assertFalse(list(target.parent.glob(".handoff-*.bak")))

    def test_failed_update_publication_restores_previous_bytes(self):
        import handoff

        created = handoff.update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
        )
        target = self.root / created["path"]
        before = target.read_bytes()
        real_replace = handoff.os.replace
        failed = False

        def fail_new_publication(source, destination):
            nonlocal failed
            if not failed and str(source).endswith(".tmp"):
                failed = True
                raise OSError("injected publication failure")
            return real_replace(source, destination)

        with mock.patch.object(
            handoff.os,
            "replace",
            side_effect=fail_new_publication,
        ):
            with self.assertRaisesRegex(OSError, "injected publication failure"):
                handoff.update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "second",
                    expected_sha256=created["sha256"],
                    workspace="personal",
                )

        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(list(target.parent.glob(".handoff-*.tmp")))
        self.assertFalse(list(target.parent.glob(".handoff-*.bak")))

    def test_failed_temp_write_leaves_no_residue(self):
        import handoff

        target_dir = self.root / "projects/research-agent-memory"
        with mock.patch.object(
            handoff.os,
            "write",
            side_effect=OSError("injected short write"),
        ):
            with self.assertRaisesRegex(ValueError, "handoff write failed"):
                handoff.update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                )

        self.assertFalse(list(target_dir.glob(".handoff-*.tmp")))
        self.assertFalse((target_dir / "handoff.md").exists())

    def test_symlink_target_is_rejected_without_touching_external_file(self):
        from handoff import update_project_handoff

        external = Path(self.temp.name) / "external.md"
        external.write_text("external", encoding="utf-8")
        target = self.root / "projects/research-agent-memory/handoff.md"
        target.parent.mkdir(parents=True)
        target.symlink_to(external)

        with self.assertRaisesRegex(ValueError, "invalid handoff target"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "replacement",
                workspace="personal",
            )

        self.assertEqual(external.read_text(encoding="utf-8"), "external")
        self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main()
