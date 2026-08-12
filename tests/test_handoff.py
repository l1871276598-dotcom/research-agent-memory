import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
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
        self.state = Path(self.temp.name) / "state"
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
            state_dir=self.state,
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

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
            state_dir=self.state,
        )
        target = self.root / created["path"]
        before = hashlib.sha256(target.read_bytes()).hexdigest()

        updated = update_project_handoff(
            self.root,
            "research-agent-memory",
            "second",
            expected_sha256=before,
            workspace="personal",
            state_dir=self.state,
        )

        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["previous_sha256"], before)
        self.assertIn("second", target.read_text(encoding="utf-8"))

    def test_rejects_missing_source_backed_project_without_writing(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "project_slug_not_source_backed"):
            update_project_handoff(
                self.root,
                "unknown-project",
                "content",
                workspace="personal",
                state_dir=self.state,
            )

        self.assertFalse((self.root / "projects/unknown-project/handoff.md").exists())

    def test_source_backed_project_scan_uses_only_authority_authorized_records(self):
        from handoff import update_project_handoff
        from review.authority import AuthorityStore

        with mock.patch.object(AuthorityStore, "authorize_active_records", return_value=[]):
            with self.assertRaisesRegex(ValueError, "project_slug_not_source_backed"):
                update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                    state_dir=self.state,
                )

        self.assertFalse((self.root / "projects/research-agent-memory/handoff.md").exists())

    def test_pending_activation_blocks_handoff_write(self):
        from handoff import update_project_handoff
        from review.authority import AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        (authority.pending / ("mdec_" + "0" * 64 + ".json")).write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "pending activation"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "content",
                workspace="personal",
                state_dir=self.state,
            )

        self.assertFalse((self.root / "projects/research-agent-memory/handoff.md").exists())

    def test_authority_construction_error_blocks_without_raw_record_fallback(self):
        from handoff import update_project_handoff

        with mock.patch(
            "review.authority.AuthorityStore",
            side_effect=RuntimeError("authority construction failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "authority construction failed"):
                update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                    state_dir=self.state,
                )

        self.assertFalse((self.root / "projects/research-agent-memory/handoff.md").exists())

    def test_rejects_other_project_handoff_without_overwrite(self):
        from handoff import update_project_handoff

        target = self.root / "projects/research-agent-memory/handoff.md"
        target.parent.mkdir(parents=True)
        target.write_text('---\nproject_slug: "other-project"\n---\n\nold\n', encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "target_handoff_belongs_to_another_project"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "new",
                workspace="personal",
                state_dir=self.state,
            )

        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_rejects_sha_mismatch_without_overwrite(self):
        from handoff import update_project_handoff

        update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
            state_dir=self.state,
        )
        target = self.root / "projects/research-agent-memory/handoff.md"
        before = target.read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "second",
                expected_sha256="0" * 64,
                workspace="personal",
                state_dir=self.state,
            )

        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_rejects_cross_workspace_project_without_writing(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "project_not_in_workspace"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "content",
                workspace="work",
                state_dir=self.state,
            )

        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

    def test_accepts_matching_workspace_explicitly(self):
        from handoff import update_project_handoff

        result = update_project_handoff(
            self.root,
            "research-agent-memory",
            "content",
            workspace="personal",
            state_dir=self.state,
        )
        self.assertEqual(result["status"], "created")

    def test_rejects_invalid_workspace_value(self):
        from handoff import update_project_handoff

        with self.assertRaisesRegex(ValueError, "workspace must be personal or work"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "content",
                workspace="internal",
                state_dir=self.state,
            )

    def test_agent_enforces_task_workspace(self):
        from agents.handoff import HandoffAgent

        agent = HandoffAgent(self.root, self.state)
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

        agent = HandoffAgent(self.root, self.state)
        task = {
            "type": "handoff.write",
            "input": {"project_slug": "research-agent-memory", "content": "content"},
        }
        with self.assertRaises(ValueError):
            agent.run(task, {})

    def test_core_requires_explicit_workspace(self):
        from handoff import update_project_handoff

        with self.assertRaises(TypeError):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "content",
                state_dir=self.state,
            )

    def _facade(self):
        from tool_facade import LaosToolFacade
        from agents.handoff import HandoffAgent

        agent = HandoffAgent(self.root, self.state)

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
            state_dir=self.state,
        )
        target = self.root / created["path"]
        before = target.read_bytes()

        with self.assertRaisesRegex(ValueError, "expected_sha256 is required"):
            update_project_handoff(
                self.root,
                "research-agent-memory",
                "second",
                workspace="personal",
                state_dir=self.state,
            )

        self.assertEqual(target.read_bytes(), before)

    def test_receipt_binds_before_after_and_recomputes(self):
        from handoff import update_project_handoff

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
            state_dir=self.state,
        )
        project = self.root / "memory/projects/project-research-agent-memory-test.md"
        self.assertEqual(created["authority_generation"], 0)
        self.assertEqual(
            created["authorized_project_binding"],
            {
                "memory_id": "project-research-agent-memory",
                "relative_path": project.relative_to(self.root).as_posix(),
                "artifact_sha256": hashlib.sha256(project.read_bytes()).hexdigest(),
                "activation_id": None,
            },
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
            state_dir=self.state,
        )
        self.assertEqual(updated["before_sha256"], created["sha256"])
        self.assertEqual(updated["previous_sha256"], created["sha256"])
        self.assertEqual(updated["after_sha256"], updated["sha256"])
        self.assertEqual(updated["expected_sha256"], created["sha256"])

    def test_authority_generation_change_between_authorization_and_publication_is_rejected(self):
        import handoff
        from review.authority import AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        real_handoff_lock = handoff._handoff_lock

        @contextmanager
        def generation_changes_after_authorization(root, staging_dir):
            with real_handoff_lock(root, staging_dir):
                authority._set_generation(0, 1)
                yield

        with mock.patch.object(
            handoff, "_handoff_lock", generation_changes_after_authorization
        ):
            with self.assertRaisesRegex(ValueError, "handoff authority changed"):
                handoff.update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                    state_dir=self.state,
                )

        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

    def test_pending_activation_created_during_handoff_staging_is_rejected(self):
        import handoff
        from review.authority import AuthorityStore

        authority = AuthorityStore(self.root, self.state)
        real_write_temp = handoff._write_temp

        def stage_then_mark_pending(parent, rendered):
            temp = real_write_temp(parent, rendered)
            (authority.pending / ("mdec_" + "0" * 64 + ".json")).write_text(
                "{}", encoding="utf-8"
            )
            return temp

        with mock.patch.object(handoff, "_write_temp", side_effect=stage_then_mark_pending):
            with self.assertRaisesRegex(ValueError, "pending activation"):
                handoff.update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                    state_dir=self.state,
                )

        target_dir = self.root / "projects/research-agent-memory"
        self.assertFalse((target_dir / "handoff.md").exists())
        self.assertFalse(list(target_dir.glob(".handoff-*.tmp")))

    def test_project_artifact_changed_during_handoff_staging_is_rejected(self):
        import handoff
        from review.authority import AuthorityError

        project = self.root / "memory/projects/project-research-agent-memory-test.md"
        real_write_temp = handoff._write_temp

        def stage_then_change_project(parent, rendered):
            temp = real_write_temp(parent, rendered)
            project.write_text(
                project.read_text(encoding="utf-8").replace(
                    "Project research-agent-memory", "Changed project", 1
                ),
                encoding="utf-8",
            )
            return temp

        with mock.patch.object(handoff, "_write_temp", side_effect=stage_then_change_project):
            with self.assertRaises(AuthorityError) as raised:
                handoff.update_project_handoff(
                    self.root,
                    "research-agent-memory",
                    "content",
                    workspace="personal",
                    state_dir=self.state,
                )

        self.assertEqual(raised.exception.code, "activation_receipt_missing")
        self.assertFalse(
            (self.root / "projects/research-agent-memory/handoff.md").exists()
        )

    def test_receipt_binds_activation_backed_project_authorization(self):
        from handoff import update_project_handoff
        import memory
        from memory.candidate import CandidateStore
        from review.gate import ReviewGate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            state = Path(tmp) / "state"
            memory.init_store(root)
            memory.db_init(argparse.Namespace(root=str(root), state_dir=str(state)))
            created = CandidateStore(root, state).create(
                {
                    "type": "project",
                    "title": "Project one",
                    "scope": "project",
                    "workspace": "personal",
                    "confidentiality": "personal",
                    "source": "manual:user_confirmed",
                    "confidence": "confirmed",
                    "content": "project one",
                    "action": "create",
                    "project": "project-one",
                    "tags": [],
                }
            )
            gate = ReviewGate(root, state)
            decision = gate.review("accept", created["candidate_id"])
            activation = gate.activate(
                decision["decision_id"], decision["expected_active_generation"]
            )

            result = update_project_handoff(
                root,
                "project-one",
                "content",
                workspace="personal",
                state_dir=state,
            )

            project = root / created["path"]
            self.assertEqual(result["authority_generation"], 1)
            self.assertEqual(
                result["authorized_project_binding"],
                {
                    "memory_id": created["candidate_id"],
                    "relative_path": created["path"],
                    "artifact_sha256": hashlib.sha256(project.read_bytes()).hexdigest(),
                    "activation_id": activation["activation_id"],
                },
            )

    def test_same_expected_sha256_allows_only_one_concurrent_update(self):
        from handoff import update_project_handoff

        created = update_project_handoff(
            self.root,
            "research-agent-memory",
            "first",
            workspace="personal",
            state_dir=self.state,
        )
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(SOURCE_DIR)!r});"
            "from handoff import update_project_handoff;"
            "root,content,expected,state_dir=sys.argv[1:5];"
            "\ntry:\n"
            " r=update_project_handoff(root,'research-agent-memory',content,expected,workspace='personal',state_dir=state_dir);"
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
                str(self.state),
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
            state_dir=self.state,
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
                    state_dir=self.state,
                )

        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(list(target.parent.glob(".handoff-*.tmp")))
        self.assertFalse(list(target.parent.glob(".handoff-*.bak")))

    def test_failed_temp_write_leaves_no_residue(self):
        import handoff
        from review.authority import AuthorityStore

        AuthorityStore(self.root, self.state)
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
                    state_dir=self.state,
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
                state_dir=self.state,
            )

        self.assertEqual(external.read_text(encoding="utf-8"), "external")
        self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main()
