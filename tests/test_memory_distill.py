import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY = REPO_ROOT / "src" / "memory.py"
DISTILL = REPO_ROOT / "src" / "memory_distill.py"
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_distill_module():
    spec = importlib.util.spec_from_file_location("memory_distill_for_tests", DISTILL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemoryDistillReviewTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, *map(str, args)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def init_root(self, tmp):
        root = Path(tmp) / "data"
        self.assertEqual(self.run_cli(MEMORY, "init", "--root", root).returncode, 0)
        self.assertEqual(
            self.run_cli(MEMORY, "db-init", "--root", root, "--state-dir", root.parent / "state").returncode,
            0,
        )
        return root

    def add_memory(self, root, title="目标记忆", content="旧内容", **kwargs):
        args = [
            MEMORY,
            "add",
            "--confirmed",
            "--root",
            root,
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            title,
            "--scope",
            kwargs.pop("scope", "global"),
            "--workspace",
            kwargs.pop("workspace", "personal"),
            "--confidentiality",
            kwargs.pop("confidentiality", "personal"),
            "--source",
            "user",
            "--confidence",
            "confirmed",
            "--content",
            content,
        ]
        for option, value in kwargs.items():
            if value is not None:
                args.extend(["--" + option.replace("_", "-"), value])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        memory_id = re.search(r"memory_id: (.+)", result.stdout).group(1)
        accepted = self.distill_accept(root, memory_id)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        return memory_id

    def distill_apply(self, root, action="create", **kwargs):
        if "source_path" not in kwargs:
            source_dir = root / "imports/manual/text"
            source_dir.mkdir(parents=True, exist_ok=True)
            sequence = len(list(source_dir.glob("test-evidence-*.md")))
            source_path = source_dir / f"test-evidence-{sequence:04d}.md"
            source_path.write_text(
                f"verified test evidence {sequence}\n", encoding="utf-8"
            )
            kwargs["source_path"] = source_path.relative_to(root).as_posix()
        if "source_sha256" not in kwargs:
            source_path = root / kwargs["source_path"]
            kwargs["source_sha256"] = hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest()
        args = [
            DISTILL,
            "apply",
            "--root",
            root,
            "--state-dir",
            root.parent / "state",
            "--action",
            action,
            "--type",
            kwargs.pop("memory_type", "principle"),
            "--title",
            kwargs.pop("title", "候选记忆"),
            "--scope",
            kwargs.pop("scope", "global"),
            "--workspace",
            kwargs.pop("workspace", "personal"),
            "--confidentiality",
            kwargs.pop("confidentiality", "personal"),
            "--source",
            kwargs.pop("source", "codex"),
            "--content",
            kwargs.pop("content", "候选内容"),
        ]
        kwargs.setdefault("source_refs", ["manual:test-evidence"])
        for option, value in kwargs.items():
            flag = "--" + option.replace("_", "-")
            if isinstance(value, list):
                args.append(flag)
                args.extend(value)
            elif value is not None:
                args.extend([flag, value])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return re.search(r"candidate_id: (.+)", result.stdout).group(1)

    def distill_accept(self, root, candidate_id):
        record = self.parsed_records(root)[candidate_id][1]
        reviewer_confidentiality = record["confidentiality"]
        if reviewer_confidentiality == "public":
            reviewer_confidentiality = "personal"
        decision = self.run_cli(
            DISTILL,
            "accept",
            "--root",
            root,
            "--state-dir",
            root.parent / "state",
            "--id",
            candidate_id,
            "--reviewer-workspace",
            record["workspace"],
            "--reviewer-confidentiality",
            reviewer_confidentiality,
        )
        if decision.returncode != 0:
            return decision
        decision_id = re.search(r"Decision: (.+)", decision.stdout).group(1)
        generation = re.search(
            r"Expected active generation: (\d+)", decision.stdout
        ).group(1)
        return self.run_cli(
            DISTILL,
            "activate",
            "--root",
            root,
            "--state-dir",
            root.parent / "state",
            "--decision-id",
            decision_id,
            "--expected-active-generation",
            generation,
        )

    def distill_reject(self, root, candidate_id, reason="not useful"):
        record = self.parsed_records(root)[candidate_id][1]
        reviewer_confidentiality = record["confidentiality"]
        if reviewer_confidentiality == "public":
            reviewer_confidentiality = "personal"
        decision = self.run_cli(
            DISTILL,
            "reject",
            "--root",
            root,
            "--state-dir",
            root.parent / "state",
            "--id",
            candidate_id,
            "--reason",
            reason,
            "--reviewer-workspace",
            record["workspace"],
            "--reviewer-confidentiality",
            reviewer_confidentiality,
        )
        if decision.returncode != 0:
            return decision
        decision_id = re.search(r"Decision: (.+)", decision.stdout).group(1)
        generation = re.search(
            r"Expected active generation: (\d+)", decision.stdout
        ).group(1)
        return self.run_cli(
            DISTILL,
            "activate",
            "--root",
            root,
            "--state-dir",
            root.parent / "state",
            "--decision-id",
            decision_id,
            "--expected-active-generation",
            generation,
        )

    def record_by_id(self, root, memory_id):
        for path in (root / "memory").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if re.search(rf'^id: "{re.escape(memory_id)}"$', text, re.MULTILINE):
                return path, text
        self.fail(f"missing memory {memory_id}")

    def parsed_records(self, root):
        from memory import parse_front_matter
        rows = {}
        for path in (root / "memory").rglob("*.md"):
            record, errors = parse_front_matter(path)
            self.assertEqual(errors, [], path)
            rows[record["id"]] = (path, record)
        return rows

    def transition_apply(self, root, **kwargs):
        args = [
            MEMORY, "context-transition", "--root", root,
            "--from-context", kwargs.pop("from_context", "university-student"),
            "--to-context", kwargs.pop("to_context", "industry-engineer"),
            "--to-title", kwargs.pop("to_title", "企业研发阶段"),
            "--workspace", kwargs.pop("workspace", "personal"),
            "--confidentiality", kwargs.pop("confidentiality", "personal"),
            "--effective-date", kwargs.pop("effective_date", "2027-07-01"),
            "--reason", kwargs.pop("reason", "进入企业研发岗位"),
            "--source", kwargs.pop("source", "codex"),
            "--confidence", kwargs.pop("confidence", "inferred"),
        ]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidates = [
            record["id"] for _, record in self.parsed_records(root).values()
            if record.get("type") == "context_transition" and record.get("status") == "candidate"
        ]
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def test_apply_creates_candidate_not_active_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)

            candidate_id = self.distill_apply(root, evidence=["manual:imports/manual/text/a.md"])
            path, text = self.record_by_id(root, candidate_id)

            self.assertIn('status: "candidate"', text)
            self.assertIn('audit_status: "awaiting_review"', text)
            self.assertIn('candidate_action: "ADD"', text)
            self.assertIn('requested_action: "create"', text)
            self.assertIn('confidence: "inferred"', text)
            self.assertTrue(path.name.startswith(candidate_id + "-"))

    def test_accept_create_activates_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root, action="create")

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "active"', text)
            self.assertIn('audit_status: "accepted"', text)
            self.assertIn("reviewed_at:", text)

    def test_accept_merge_only_merges_safe_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, title="目标", content="核心内容")
            candidate_id = self.distill_apply(root, action="merge", target_id=target_id, tags=["new"], source_refs=["manual:doc-one"], relations=["supports:doc"])

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn("核心内容", target)
            self.assertIn("source_refs:", target)
            self.assertIn('  - "manual:doc-one"', target)
            self.assertIn("relations:", target)
            self.assertIn('status: "archived"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)

    def test_accept_support_does_not_change_target_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, content="do not overwrite")
            candidate_id = self.distill_apply(root, action="support", target_id=target_id, content="replacement", evidence=["manual:doc-two"])

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            self.assertIn("do not overwrite", target)
            self.assertNotIn("replacement", target)
            self.assertIn('  - "manual:doc-two"', target)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn('status: "archived"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)

    def test_accept_supersede_links_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root)
            candidate_id = self.distill_apply(root, action="supersede", target_id=target_id)

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn('status: "deprecated"', target)
            self.assertIn(f'  - "{candidate_id}"', target)
            self.assertIn('status: "active"', candidate)
            self.assertIn('audit_status: "accepted"', candidate)
            self.assertIn(f'  - "{target_id}"', candidate)

    def test_accept_conflict_preserves_target_and_marks_audit_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, content="confirmed content")
            candidate_id = self.distill_apply(root, action="conflict", target_id=target_id, content="conflicting content")

            result = self.distill_accept(root, candidate_id)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, target = self.record_by_id(root, target_id)
            _, candidate = self.record_by_id(root, candidate_id)
            self.assertIn("confirmed content", target)
            self.assertNotIn("conflicting content", target)
            self.assertIn('status: "conflicted"', candidate)
            self.assertIn('audit_status: "conflict"', candidate)

    def test_reject_records_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root)

            result = self.distill_reject(root, candidate_id, "weak evidence")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "archived"', text)
            self.assertIn('audit_status: "rejected"', text)
            self.assertIn('review_reason: "weak evidence"', text)

    def test_reject_reindexes_indexed_candidate_to_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root)

            indexed = self.run_cli(
                MEMORY, "index", "--root", root, "--state-dir", root.parent / "state"
            )
            self.assertEqual(indexed.returncode, 0, indexed.stdout + indexed.stderr)
            with closing(sqlite3.connect(root.parent / "state/memory.sqlite")) as conn:
                self.assertEqual(
                    conn.execute("SELECT status FROM memories WHERE id=?", (candidate_id,)).fetchone(),
                    ("candidate",),
                )

            result = self.distill_reject(root, candidate_id, "weak evidence")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with closing(sqlite3.connect(root.parent / "state/memory.sqlite")) as conn:
                self.assertEqual(
                    conn.execute("SELECT status FROM memories WHERE id=?", (candidate_id,)).fetchone(),
                    ("archived",),
                )

    def test_accept_blocks_changed_source_hash_and_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            source = root / "imports/manual/text/source.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("original", encoding="utf-8")
            target_id = self.add_memory(root)
            candidate_id = self.distill_apply(
                root,
                action="merge",
                target_id=target_id,
                source_path="imports/manual/text/source.md",
                source_sha256="0682c5f2076f099c34cfdd15a9e063849ed437a49677e6fcc5b4198c76575be5",
            )
            source.write_text("changed", encoding="utf-8")

            result = self.distill_accept(root, candidate_id)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source hash changed", result.stderr)

            source.write_text("original", encoding="utf-8")
            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "UPDATE", "--target-id", "missing-target", "--type", "principle",
                "--title", "Missing target", "--scope", "global", "--workspace", "personal",
                "--confidentiality", "personal", "--source", "codex",
                "--source-refs", "manual:test", "--content", "new",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target memory not found", result.stderr)

    def test_target_mutation_proposal_rejects_cross_project_or_context_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            self.add_memory(
                root,
                title="Project A",
                content="project a",
                memory_type="project",
                scope="project",
                project="project-a",
            )
            self.add_memory(
                root,
                title="Project B",
                content="project b",
                memory_type="project",
                scope="project",
                project="project-b",
            )
            self.add_memory(
                root,
                title="Context A",
                content="context a",
                memory_type="context",
                scope="context",
                context_id="context-a",
            )
            self.distill_apply(
                root,
                action="create",
                title="Context B",
                content="context b",
                memory_type="context",
                scope="context",
                context_id="context-b",
            )
            target_id = self.add_memory(
                root,
                title="Target",
                content="target content",
                scope="project",
                project="project-a",
                context_id="context-a",
            )
            for field, replacement in (
                ("project", "project-b"),
                ("context-id", "context-b"),
            ):
                with self.subTest(field=field):
                    result = self.run_cli(
                        DISTILL,
                        "apply",
                        "--root",
                        root,
                        "--state-dir",
                        root.parent / "state",
                        "--action",
                        "UPDATE",
                        "--target-id",
                        target_id,
                        "--type",
                        "principle",
                        "--title",
                        f"Cross {field}",
                        "--scope",
                        "project",
                        "--workspace",
                        "personal",
                        "--confidentiality",
                        "personal",
                        "--project",
                        replacement if field == "project" else "project-a",
                        "--context-id",
                        replacement if field == "context-id" else "context-a",
                        "--source",
                        "manual:user_confirmed",
                        "--confirmed",
                        "--content",
                        f"cross {field} candidate",
                    )

                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("target memory not found", result.stderr)
                    self.assertFalse(
                        any(
                            record.get("title") == f"Cross {field}"
                            for _, record in self.parsed_records(root).values()
                        )
                    )

    def test_accept_rejects_stale_target_before_index_or_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root)
            candidate_id = self.distill_apply(root, action="UPDATE", target_id=target_id)
            records = self.parsed_records(root)
            target_path, _ = records[target_id]
            candidate = records[candidate_id][1]
            self.assertEqual(candidate["expected_target_status"], "active")
            self.assertEqual(candidate["expected_target_sha256"], hashlib.sha256(target_path.read_bytes()).hexdigest())
            target_path.write_text(
                target_path.read_text(encoding="utf-8").replace('status: "active"', 'status: "deprecated"', 1),
                encoding="utf-8",
            )
            before = {path: path.read_bytes() for path in (root / "memory").rglob("*.md")}
            db = root.parent / "state/memory.sqlite"
            db_before = db.read_bytes()
            module = load_distill_module()
            args = argparse.Namespace(root=str(root), state_dir=str(root.parent / "state"), id=candidate_id)
            journal_path = root / "state/distill_runs.jsonl"
            journal_before = journal_path.read_bytes()

            with mock.patch.object(module, "index_store") as index_store:
                with self.assertRaises(module.DistillError) as raised:
                    module.accept_candidate(args)

            self.assertEqual(raised.exception.feedback["code"], "stale_target")
            self.assertEqual(raised.exception.feedback["recoverable"], False)
            self.assertEqual(raised.exception.feedback["next_action"], "regenerate_proposal")
            index_store.assert_not_called()
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(db.read_bytes(), db_before)
            self.assertEqual(journal_path.read_bytes(), journal_before)

    def test_accept_rejects_changed_or_deleted_target_as_stale(self):
        for mutation in ("content", "delete"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = self.init_root(tmp)
                target_id = self.add_memory(root)
                candidate_id = self.distill_apply(root, action="UPDATE", target_id=target_id)
                target_path = self.parsed_records(root)[target_id][0]
                if mutation == "content":
                    target_path.write_text(
                        target_path.read_text(encoding="utf-8").replace("旧内容", "外部变更", 1),
                        encoding="utf-8",
                    )
                else:
                    target_path.unlink()
                result = self.distill_accept(root, candidate_id)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('"code": "stale_target"', result.stderr)
                self.assertIn('status: "candidate"', self.record_by_id(root, candidate_id)[1])

    def test_context_transition_accept_is_one_atomic_lifecycle_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            source_id = self.add_memory(
                root, title="研究生阶段", content="当前处于研究生科研阶段。",
                memory_type="context", scope="context", context_id="university-student",
                valid_from="2023-09-01",
            )
            candidate_id = self.transition_apply(root)
            before = self.parsed_records(root)
            self.assertEqual(before[source_id][1]["status"], "active")
            self.assertEqual(before[candidate_id][1]["status"], "candidate")
            self.assertEqual(len(before), 2)

            accepted = self.distill_accept(root, candidate_id)

            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            after = self.parsed_records(root)
            old = after[source_id][1]
            transition = after[candidate_id][1]
            new_rows = [record for _, record in after.values() if record.get("context_id") == "industry-engineer"]
            self.assertEqual(len(new_rows), 1)
            new = new_rows[0]
            self.assertEqual(old["status"], "deprecated")
            self.assertEqual(new["status"], "active")
            self.assertEqual(transition["status"], "archived")
            self.assertEqual(transition["audit_status"], "accepted")
            self.assertEqual(old["superseded_by"], [new["id"]])
            self.assertEqual(new["supersedes"], [old["id"]])

    def test_context_transition_index_failure_restores_all_three_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            self.add_memory(
                root, title="研究生阶段", content="当前处于研究生科研阶段。",
                memory_type="context", scope="context", context_id="university-student",
                valid_from="2023-09-01",
            )
            candidate_id = self.transition_apply(root)
            before = {path.relative_to(root): path.read_bytes() for path in (root / "memory").rglob("*.md")}
            module = load_distill_module()
            from review.gate import ReviewGate

            gate = ReviewGate(
                root,
                root.parent / "state",
                backend=module._review_backend(),
            )
            decision = gate.review("accept", candidate_id)

            with mock.patch.object(module, "index_store", side_effect=ValueError("injected index failure")):
                with self.assertRaises(module.DistillError) as raised:
                    gate.activate(
                        decision["decision_id"],
                        decision["expected_active_generation"],
                    )

            self.assertEqual(raised.exception.feedback["code"], "index_failed")
            after = {path.relative_to(root): path.read_bytes() for path in (root / "memory").rglob("*.md")}
            self.assertEqual(after, before)
            entries = [json.loads(line) for line in (root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[-1]["status"], "index_failed")

    def test_conflict_review_index_failure_restores_candidate_and_appends_failure_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, content="confirmed content")
            candidate_id = self.distill_apply(root, action="conflict", target_id=target_id, content="conflicting content")
            before = {path.relative_to(root): path.read_bytes() for path in (root / "memory").rglob("*.md")}
            module = load_distill_module()
            from review.gate import ReviewGate

            gate = ReviewGate(root, root.parent / "state", backend=module._review_backend())
            decision = gate.review("conflict", candidate_id)

            with mock.patch.object(module, "index_store", side_effect=ValueError("injected index failure")):
                with self.assertRaises(module.DistillError) as raised:
                    gate.activate(
                        decision["decision_id"],
                        decision["expected_active_generation"],
                    )

            self.assertEqual(raised.exception.feedback["code"], "index_failed")
            after = {path.relative_to(root): path.read_bytes() for path in (root / "memory").rglob("*.md")}
            self.assertEqual(after, before)
            entries = [json.loads(line) for line in (root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[-1]["status"], "index_failed")
            self.assertEqual(entries[-1]["proposal_ids"], [candidate_id])

    def test_unrelated_change_allows_accept_and_regenerated_stale_proposal_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, title="Target")
            unrelated_id = self.add_memory(root, title="Unrelated")
            candidate_id = self.distill_apply(root, action="UPDATE", target_id=target_id, title="Replacement")
            unrelated_path = self.parsed_records(root)[unrelated_id][0]
            unrelated_path.write_text(
                unrelated_path.read_text(encoding="utf-8").replace("旧内容", "unrelated change", 1),
                encoding="utf-8",
            )
            accepted = self.distill_accept(root, candidate_id)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            target_id = self.add_memory(root, title="Target")
            stale_id = self.distill_apply(root, action="UPDATE", target_id=target_id, title="Replacement")
            target_path = self.parsed_records(root)[target_id][0]
            target_path.write_text(
                target_path.read_text(encoding="utf-8").replace("旧内容", "current target", 1),
                encoding="utf-8",
            )
            stale = self.distill_accept(root, stale_id)
            self.assertNotEqual(stale.returncode, 0)
            fresh_id = self.distill_apply(
                root, action="UPDATE", target_id=target_id,
                title="Replacement fresh", content="fresh replacement",
            )
            accepted = self.distill_accept(root, fresh_id)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_canonical_noop_does_not_create_duplicate_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            self.add_memory(root, title="Same", content="identical")
            before = sorted((root / "memory").rglob("*.md"))

            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--type", "principle", "--title", "Same",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--confirmed", "--content", "identical",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Action: NOOP", result.stdout)
            self.assertIn("Status: no_change", result.stdout)
            self.assertEqual(sorted((root / "memory").rglob("*.md")), before)

    def test_same_title_different_content_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            self.add_memory(root, title="Ambiguous", content="old")

            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--type", "principle", "--title", "Ambiguous",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--confirmed", "--content", "different",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Action: REVIEW_REQUIRED", result.stdout)
            self.assertIn("Status: waiting_for_human", result.stdout)
            entry = json.loads((root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(entry["stop_reason"], "conflict_requires_review")

    def test_uncertain_proposal_stops_low_confidence_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--type", "principle", "--title", "Uncertain",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--confirmed", "--confidence", "uncertain",
                "--content", "uncertain content",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Status: low_confidence", result.stdout)
            entry = json.loads((root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(entry["stop_reason"], "low_confidence")

    def test_canonical_update_and_deprecate_preserve_old_records(self):
        for action in ("UPDATE", "DEPRECATE"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                root = self.init_root(tmp)
                target_id = self.add_memory(root, title=f"Old {action}", content=f"old {action}")
                candidate_id = self.distill_apply(
                    root, action=action, target_id=target_id,
                    title=f"New {action}", content=f"new {action}",
                )
                result = self.distill_accept(root, candidate_id)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                _, target = self.record_by_id(root, target_id)
                _, candidate = self.record_by_id(root, candidate_id)
                self.assertIn('status: "deprecated"', target)
                self.assertIn('status: "active"', candidate)
                self.assertIn(f'  - "{candidate_id}"', target)
                self.assertIn(f'  - "{target_id}"', candidate)

    def test_confirmed_user_add_remains_candidate_until_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--confirmed", "--type", "principle", "--title", "Confirmed probe",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--content", "TrustedConfirmedProbe",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            candidate_id = re.search(r"candidate_id: (.+)", result.stdout).group(1)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "candidate"', text)
            self.assertIn('confirmation: "explicit"', text)
            self.assertNotIn('status: "active"', text)

            accepted = self.distill_accept(root, candidate_id)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            _, text = self.record_by_id(root, candidate_id)
            self.assertIn('status: "active"', text)
            with closing(sqlite3.connect(root.parent / "state/memory.sqlite")) as conn:
                self.assertEqual(conn.execute("SELECT status FROM memories WHERE id=?", (candidate_id,)).fetchone(), ("active",))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_fts WHERE id=?", (candidate_id,)).fetchone()[0], 1)
            journal = root / "state/distill_runs.jsonl"
            entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[-1]["status"], "completed")

    def test_missing_source_stops_after_two_identical_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--type", "principle", "--title", "No source",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "codex", "--content", "missing provenance",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("repeated_failure", result.stderr)
            entries = [json.loads(line) for line in (root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry["iteration"] for entry in entries[-3:]], [1, 2, 2])
            self.assertEqual(entries[-1]["stop_reason"], "repeated_failure")

    def test_dry_run_writes_no_candidate_or_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            before = sorted((root / "memory").rglob("*.md"))
            result = self.run_cli(
                DISTILL, "apply", "--dry-run", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--type", "principle", "--title", "Dry run",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--confirmed", "--content", "dry",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted((root / "memory").rglob("*.md")), before)
            self.assertFalse((root / "state/distill_runs.jsonl").exists())

    def test_accept_index_failure_restores_candidate_and_records_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            candidate_id = self.distill_apply(root, action="create")
            path, before = self.record_by_id(root, candidate_id)
            module = load_distill_module()
            from review.gate import ReviewGate

            gate = ReviewGate(
                root,
                root.parent / "state",
                backend=module._review_backend(),
            )
            decision = gate.review("accept", candidate_id)

            with mock.patch.object(module, "index_store", side_effect=ValueError("injected index failure")):
                with self.assertRaises(module.DistillError):
                    gate.activate(
                        decision["decision_id"],
                        decision["expected_active_generation"],
                    )

            self.assertEqual(path.read_text(encoding="utf-8"), before)
            entries = [json.loads(line) for line in (root / "state/distill_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(entries[-1]["status"], "index_failed")

    def test_resume_completed_run_is_read_only_and_malformed_tail_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_root(tmp)
            result = self.run_cli(
                DISTILL, "apply", "--root", root, "--state-dir", root.parent / "state",
                "--action", "ADD", "--confirmed", "--type", "principle", "--title", "Resume probe",
                "--scope", "global", "--workspace", "personal", "--confidentiality", "personal",
                "--source", "manual:user_confirmed", "--content", "ResumeCompletedProbe",
            )
            run_id = re.search(r"Run: (.+)", result.stdout).group(1)
            candidate_id = re.search(r"candidate_id: (.+)", result.stdout).group(1)
            accepted = self.distill_accept(root, candidate_id)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            before = {path: path.read_bytes() for path in (root / "memory").rglob("*.md")}
            resumed = self.run_cli(DISTILL, "resume", "--root", root, "--run-id", run_id)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertIn("Status: completed", resumed.stdout)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

            with (root / "state/distill_runs.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{malformed\n")
            malformed = self.run_cli(DISTILL, "resume", "--root", root, "--run-id", run_id)
            self.assertNotEqual(malformed.returncode, 0)
            self.assertIn("malformed journal", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
