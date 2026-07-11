import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
