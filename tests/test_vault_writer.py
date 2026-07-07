"""Phase 2 vault writer — Candidate creation tests.

Tests for Sliver 1+2: create candidate, safe write to 0-inbox/laos-generated/,
record pending_review, audit candidate.created event.

Contract: _system/contracts/02-note-identity-and-frontmatter.md
          _system/contracts/03-scan-index-and-conflict-rules.md
          _system/contracts/04-review-verification-and-context-pack.md
"""

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


def _sample_candidate_content() -> str:
    return (
        "---\n"
        "id: 01J7RM4A8KX8P1Z2Y3W4V5B6N7\n"
        "schema_version: 1\n"
        "type: project\n"
        "lifecycle: active\n"
        "source: laos\n"
        "generated_by: laos-v0.9.0\n"
        "run_id: run_01J7...\n"
        "created: 2026-07-07\n"
        "updated: 2026-07-07\n"
        "tags:\n"
        "  - test\n"
        "---\n"
        "\n"
        "Test note body.\n"
    )


class TestSliver1And2(unittest.TestCase):
    """Candidate creation: safe write, pending_review, audit."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.laos_dir = self.vault / "0-inbox" / "laos-generated"
        self.laos_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp / "test_index.sqlite"
        # Create subdirs needed for sequential tests
        (self.vault / "1-projects").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp, ignore_errors=True)

    def _init_db(self):
        from vault_scanner.vault_writer import init_candidate_db
        init_candidate_db(self.db_path)

    def _candidate_db_path(self) -> Path:
        return self.db_path

    # ── Acceptance test 1: normal creation → pending_review ─────────

    def test_create_candidate_state_pending_review(self):
        """After successful creation, candidate state is pending_review."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()
        result = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )
        self.assertEqual(result["candidate_state"], "pending_review")
        self.assertIn("candidate_id", result)
        self.assertIn("relative_path", result)
        self.assertIn("evidence_hash", result)

    # ── Acceptance test 2: only 0-inbox/laos-generated/ ─────────────

    def test_create_candidate_writes_to_laos_generated(self):
        """Candidate file is written under 0-inbox/laos-generated/."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()
        result = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )
        rel = result["relative_path"]
        self.assertTrue(
            rel.startswith("0-inbox/laos-generated/"),
            f"Candidate path {rel} not in 0-inbox/laos-generated/",
        )
        file_path = self.vault / rel
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_text(encoding="utf-8"), content)

    # ── Acceptance test 3: reject absolute path, .., symlink escape ─

    def test_rejects_absolute_candidate_path(self):
        """Absolute candidate relative_path is rejected."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        with self.assertRaises(ValueError):
            create_candidate(
                vault_root=self.vault,
                db_path=self._candidate_db_path(),
                content=_sample_candidate_content(),
                generator="test-suite",
                generator_version="0.1.0",
                relative_hint="/etc/passwd",
            )

    def test_rejects_path_traversal(self):
        """Path with .. is rejected."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        with self.assertRaises(ValueError):
            create_candidate(
                vault_root=self.vault,
                db_path=self._candidate_db_path(),
                content=_sample_candidate_content(),
                generator="test-suite",
                generator_version="0.1.0",
                relative_hint="../../etc/passwd",
            )

    def test_rejects_symlink_escape(self):
        """Symlink pointing outside vault is rejected."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        # Create a symlink dir in candidate path that resolves outside
        outside_dir = self.temp / "outside"
        outside_dir.mkdir()
        link_path = self.laos_dir / "escape"
        link_path.symlink_to(outside_dir, target_is_directory=True)

        with self.assertRaises(ValueError):
            create_candidate(
                vault_root=self.vault,
                db_path=self._candidate_db_path(),
                content=_sample_candidate_content(),
                generator="test-suite",
                generator_version="0.1.0",
                relative_hint="escape/note.md",
            )

    # ── Acceptance test 4: existing file never overwritten ──────────

    def test_does_not_overwrite_existing_file(self):
        """Candidate creation fails if the target filename already exists."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()

        first = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        # Second with same relative_hint
        with self.assertRaises(FileExistsError):
            create_candidate(
                vault_root=self.vault,
                db_path=self._candidate_db_path(),
                content=content,
                generator="test-suite",
                generator_version="0.1.0",
                relative_hint=first["relative_path"],
            )

    def test_creates_new_id_on_name_conflict_without_hint(self):
        """Two candidates without hint both succeed (different generated names)."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()

        first = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        second = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        # Both succeed but with different paths
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(
            first["relative_path"],
            second["relative_path"],
            "Two candidates without hint should get different paths",
        )

    # ── Acceptance test 5: frontmatter has no verification state ────

    def test_frontmatter_no_verification_state(self):
        """Candidate frontmatter must not contain verification_state."""
        from vault_scanner.vault_writer import create_candidate
        from vault_scanner.parser import parse_frontmatter

        self._init_db()
        content = _sample_candidate_content()
        result = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        # Read back and verify frontmatter
        file_path = self.vault / result["relative_path"]
        fm, _ = parse_frontmatter(file_path.read_text(encoding="utf-8"))
        self.assertNotIn("verification_state", fm)
        self.assertNotIn("verified", fm)

    # ── Acceptance test 6: DB hash matches file ─────────────────────

    def test_db_hash_matches_file_hash(self):
        """Evidence hash in DB matches actual file content hash."""
        from vault_scanner.vault_writer import create_candidate
        from vault_scanner.scanner import _compute_evidence_hash

        self._init_db()
        content = _sample_candidate_content()
        result = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        # Read file and compute hash
        file_path = self.vault / result["relative_path"]
        actual_hash = _compute_evidence_hash(file_path.read_text(encoding="utf-8"))

        # Check DB
        with closing(sqlite3.connect(str(self._candidate_db_path()))) as conn:
            row = conn.execute(
                "SELECT evidence_hash FROM candidates WHERE candidate_id = ?",
                (result["candidate_id"],),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], actual_hash)
        self.assertEqual(row[0], result["evidence_hash"])

    # ── Acceptance test 7: audit event created ──────────────────────

    def test_create_audit_event_on_success(self):
        """Success produces exactly one candidate.created event."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()
        result = create_candidate(
            vault_root=self.vault,
            db_path=self._candidate_db_path(),
            content=content,
            generator="test-suite",
            generator_version="0.1.0",
        )

        with closing(sqlite3.connect(str(self._candidate_db_path()))) as conn:
            rows = conn.execute(
                "SELECT event_type, candidate_id FROM audit_events "
                "WHERE candidate_id = ? ORDER BY event_id",
                (result["candidate_id"],),
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "candidate.created")

    # ── Acceptance test 8: fail on file or DB error ─────────────────

    def test_fails_on_db_write_error(self):
        """If DB write fails, file must not remain on disk."""
        from vault_scanner.vault_writer import create_candidate

        self._init_db()
        content = _sample_candidate_content()

        # Use a db_path that is a directory → will fail to open
        bad_db_path = self.temp / "not-a-db"
        bad_db_path.mkdir()

        with self.assertRaises(Exception):
            create_candidate(
                vault_root=self.vault,
                db_path=bad_db_path,
                content=content,
                generator="test-suite",
                generator_version="0.1.0",
            )

        # Temp file should be cleaned up
        laos_files = list(self.laos_dir.iterdir())
        self.assertEqual(len(laos_files), 0,
                         f"Temp files remain: {laos_files}")


if __name__ == "__main__":
    unittest.main()


# ── Sliver 5: Conflict Detection ────────────────────────────────────


class TestSliver5ConflictDetection(unittest.TestCase):
    """Conflict detection before promotion: hash check, target check, symlink check."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.laos_dir = self.vault / "0-inbox" / "laos-generated"
        self.laos_dir.mkdir(parents=True, exist_ok=True)
        for d in ["1-projects", "2-areas"]:
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp / "test_index.sqlite"
        from vault_scanner.vault_writer import init_candidate_db
        init_candidate_db(self.db_path)

    def _content(self, doc_type: str = "project") -> str:
        return (
            "---\nid: test-conflict-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_approve_plan(self):
        """Helper: create → approve → plan."""
        from vault_scanner.vault_writer import create_candidate, review_candidate, plan_promotion
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        return cand, plan

    # ── 5a: Candidate content changed → conflicted ──────────────────

    def test_candidate_changed_after_approval_is_conflicted(self):
        """If candidate content changed after review, check_promotion_conflicts raises."""
        from vault_scanner.vault_writer import check_promotion_conflicts
        cand, plan = self._create_approve_plan()

        # Modify candidate file
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(file_path.read_text("utf-8") + "\nChanged.\n", "utf-8")

        result = check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)
        self.assertIsNotNone(result)
        self.assertEqual(result["candidate_state"], "conflicted")
        self.assertIn("candidate_hash_changed", result["reasons"])

    # ── 5b: Target file exists → conflicted ─────────────────────────

    def test_target_exists_is_conflicted(self):
        """If target path exists, check_promotion_conflicts returns conflicted."""
        from vault_scanner.vault_writer import check_promotion_conflicts
        cand, plan = self._create_approve_plan()

        # Create target file
        target = self.vault / plan["target_path"]
        target.write_text("existing\n", "utf-8")

        result = check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)
        self.assertIsNotNone(result)
        self.assertEqual(result["candidate_state"], "conflicted")
        self.assertIn("target_exists", result["reasons"])

    # ── 5c: Target is a symlink → conflicted ────────────────────────

    def test_target_is_symlink_is_conflicted(self):
        """If target path is a symlink, check_promotion_conflicts returns conflicted."""
        import os
        from vault_scanner.vault_writer import check_promotion_conflicts
        cand, plan = self._create_approve_plan()

        # Create a symlink at target path
        outside = self.temp / "outside_target.md"
        outside.write_text("outside\n", "utf-8")
        target = self.vault / plan["target_path"]
        os.symlink(str(outside), str(target))

        result = check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)
        self.assertIsNotNone(result)
        self.assertEqual(result["candidate_state"], "conflicted")
        self.assertIn("target_symlink", result["reasons"])

    # ── 5d: No conflicts → returns None ────────────────────────────

    def test_no_conflicts_returns_none(self):
        """When no conflicts, check_promotion_conflicts returns None."""
        from vault_scanner.vault_writer import check_promotion_conflicts
        cand, plan = self._create_approve_plan()
        result = check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)
        self.assertIsNone(result)

    # ── 5e: Conflict creates audit event ────────────────────────────

    def test_conflict_creates_audit_event(self):
        """Conflict detection writes promotion.conflicted audit event."""
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import check_promotion_conflicts
        cand, plan = self._create_approve_plan()

        # Create target to force conflict
        target = self.vault / plan["target_path"]
        target.write_text("existing\n", "utf-8")

        check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)

        with closing(sqlite3.connect(str(self.db_path))) as conn:
            events = conn.execute(
                "SELECT event_type FROM audit_events "
                "WHERE candidate_id = ? AND event_type = 'promotion.conflicted'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertGreaterEqual(len(events), 1)

    # ── 5f: Candidate not approved → no-op ─────────────────────────

    def test_non_approved_returns_none(self):
        """Candidate not approved → check_promotion_conflicts returns None (no-op)."""
        from vault_scanner.vault_writer import create_candidate, check_promotion_conflicts
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        result = check_promotion_conflicts(self.db_path, cand["candidate_id"], self.vault)
        self.assertIsNone(result)

    # ── 5g: Old plan with re-reviewed candidate → plan hash mismatch ----

    def test_old_plan_after_re_review_is_conflicted(self):
        """Using old plan after re-review + content change → plan hash mismatch."""
        from vault_scanner.vault_writer import (
            create_candidate, review_candidate, plan_promotion,
            check_promotion_conflicts, invalidate_candidate_if_changed,
        )

        # Create, approve, plan
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        old_plan_id = plan["plan_id"]

        # Modify candidate → invalidate → re-review with new content
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(file_path.read_text("utf-8") + "\nRe-reviewed.\n", "utf-8")
        invalidate_candidate_if_changed(self.db_path, cand["candidate_id"],
                                         vault_root=self.vault)

        # Re-review
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")

        # Run conflict check with OLD plan_id — should detect mismatch
        result = check_promotion_conflicts(
            self.db_path, cand["candidate_id"], self.vault,
            plan_id=old_plan_id,
        )
        self.assertIsNotNone(result)
        self.assertIn("candidate_plan_hash_mismatch", result["reasons"])


# ── Sliver 4: Promotion Plan ───────────────────────────────────────


class TestSliver4PromotionPlan(unittest.TestCase):
    """Promotion plan: type→dir mapping, target check, audit."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.laos_dir = self.vault / "0-inbox" / "laos-generated"
        self.laos_dir.mkdir(parents=True, exist_ok=True)
        # Create formal directories needed by tests
        for d in ["1-projects", "2-areas", "3-resources"]:
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp / "test_index.sqlite"
        self._init()

    def _init(self):
        from vault_scanner.vault_writer import init_candidate_db
        init_candidate_db(self.db_path)

    def _content_with_type(self, doc_type: str) -> str:
        return (
            "---\n"
            f"id: test-{doc_type}-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\n"
            "lifecycle: active\n"
            "source: laos\n"
            "created: 2026-07-07\n"
            "updated: 2026-07-07\n"
            "---\n"
            f"\n{doc_type} note body.\n"
        )

    def _create_and_approve(self, doc_type: str) -> dict:
        from vault_scanner.vault_writer import create_candidate, review_candidate
        content = self._content_with_type(doc_type)
        cand = create_candidate(
            vault_root=self.vault, db_path=self.db_path,
            content=content, generator="test-suite",
            generator_version="0.1.0",
        )
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        return cand

    # ── 4a: project → 1-projects/ ──────────────────────────────────

    def test_project_maps_to_1_projects(self):
        """project type → target_directory = 1-projects."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("project")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        self.assertEqual(plan["target_directory"], "1-projects")
        self.assertEqual(plan["doc_type"], "project")
        self.assertIn("1-projects/", plan["target_path"])

    # ── 4b: principle → 2-areas/ ───────────────────────────────────

    def test_principle_maps_to_2_areas(self):
        """principle type → target_directory = 2-areas."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("principle")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        self.assertEqual(plan["target_directory"], "2-areas")

    # ── 4c: reference → 3-resources/ ───────────────────────────────

    def test_reference_maps_to_3_resources(self):
        """reference type → target_directory = 3-resources."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("reference")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        self.assertEqual(plan["target_directory"], "3-resources")

    # ── 4d: meeting requires manual target ──────────────────────────

    def test_meeting_requires_manual_target(self):
        """meeting type → raises ValueError without manual_target_dir."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("meeting")
        with self.assertRaisesRegex(ValueError, "manual_target_dir"):
            plan_promotion(self.db_path, cand["candidate_id"], self.vault)

    def test_meeting_with_manual_target_succeeds(self):
        """meeting type with manual_target_dir → plan succeeds."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("meeting")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault,
                              manual_target_dir="2-areas")
        self.assertEqual(plan["target_directory"], "2-areas")

    # ── 4e: target collision detection ──────────────────────────────

    def test_target_collision_detected(self):
        """When target file exists, target_exists is True."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("project")

        # Pre-create the target file
        target_file = self.vault / "1-projects" / Path(cand["relative_path"]).name
        target_file.write_text("existing content\n", encoding="utf-8")

        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        self.assertTrue(plan["target_exists"])

    def test_target_free_if_not_exists(self):
        """When target file does not exist, target_exists is False."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("project")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        self.assertFalse(plan["target_exists"])

    # ── 4f: not-yet-approved candidate rejected ─────────────────────

    def test_non_approved_cannot_plan(self):
        """Candidate must be approved for promotion plan."""
        from vault_scanner.vault_writer import create_candidate, plan_promotion
        cand = create_candidate(
            vault_root=self.vault, db_path=self.db_path,
            content=self._content_with_type("project"),
            generator="test-suite", generator_version="0.1.0",
        )
        with self.assertRaisesRegex(ValueError, "must be 'approved'"):
            plan_promotion(self.db_path, cand["candidate_id"], self.vault)

    # ── 4g: forbidden target directory rejected ─────────────────────

    def test_forbidden_target_dir_rejected(self):
        """Target directory _system/ is forbidden for manual types."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("meeting")  # meeting needs manual_target_dir
        with self.assertRaisesRegex(ValueError, "forbidden"):
            plan_promotion(self.db_path, cand["candidate_id"], self.vault,
                           manual_target_dir="_system")

    def test_unknown_type_rejected(self):
        """Type not in auto or manual → ValueError."""
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("project")
        # Inject a candidate with unknown type by creating a file directly
        cand2 = self._create_and_approve("project")
        file_path = self.vault / cand2["relative_path"]
        content = file_path.read_text(encoding="utf-8").replace("type: project", "type: foobar")
        file_path.write_text(content, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unknown type"):
            plan_promotion(self.db_path, cand2["candidate_id"], self.vault)

    # ── 4h: audit event created ─────────────────────────────────────

    def test_promotion_plan_creates_audit_event(self):
        """plan_promotion writes promotion.planned audit event."""
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import plan_promotion
        cand = self._create_and_approve("project")
        plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            events = conn.execute(
                "SELECT event_type, target_path FROM audit_events "
                "WHERE candidate_id = ? AND event_type = 'promotion.planned'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertEqual(len(events), 1)
        self.assertIn("1-projects/", events[0][1])


# ── Sliver 3: Review Gate ──────────────────────────────────────────


REVIEWED_ATTRS = {"candidate_id", "reviewed_hash", "reviewed_at", "reviewed_by",
                  "decision", "review_comment", "before_state", "after_state"}


class TestSliver3ReviewGate(unittest.TestCase):
    """Review Gate: approve/reject, hash binding, invalidation, audit."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.laos_dir = self.vault / "0-inbox" / "laos-generated"
        self.laos_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp / "test_index.sqlite"
        self._init()

    def _init(self):
        from vault_scanner.vault_writer import init_candidate_db
        init_candidate_db(self.db_path)

    def _create_candidate(self, content: str | None = None) -> dict:
        from vault_scanner.vault_writer import create_candidate
        c = content or _sample_candidate_content()
        return create_candidate(
            vault_root=self.vault,
            db_path=self.db_path,
            content=c,
            generator="test-suite",
            generator_version="0.1.0",
        )

    # ── 3a: pending_review can be approved ──────────────────────────

    def test_review_approve(self):
        """Approving a pending_review candidate sets state to approved."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        result = review_candidate(
            self.db_path, cand["candidate_id"],
            decision="approve",
            reviewed_by="human-tester",
        )
        self.assertEqual(result["candidate_state"], "approved")
        self.assertEqual(result["decision"], "approve")

    # ── 3b: pending_review can be rejected ──────────────────────────

    def test_review_reject(self):
        """Rejecting a pending_review candidate sets state to rejected."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        result = review_candidate(
            self.db_path, cand["candidate_id"],
            decision="reject",
            reviewed_by="human-tester",
        )
        self.assertEqual(result["candidate_state"], "rejected")
        self.assertEqual(result["decision"], "reject")

    # ── 3c: review binds to evidence_hash ───────────────────────────

    def test_review_binds_hash(self):
        """Approved review records reviewed_hash = current evidence_hash."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        result = review_candidate(
            self.db_path, cand["candidate_id"],
            decision="approve",
            reviewed_by="human-tester",
        )
        self.assertEqual(result["reviewed_hash"], cand["evidence_hash"])

    # ── 3d: candidate changed after approval → invalidated ──────────

    def test_content_change_invalidates_approval(self):
        """If candidate content changes after approval, state resets to pending_review."""
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import review_candidate, invalidate_candidate_if_changed
        from vault_scanner.scanner import _compute_evidence_hash

        cand = self._create_candidate()

        # Approve
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")

        # Modify the file on disk
        file_path = self.vault / cand["relative_path"]
        original = file_path.read_text(encoding="utf-8")
        modified = original + "\nNew content.\n"
        file_path.write_text(modified, encoding="utf-8")

        # Check invalidation
        result = invalidate_candidate_if_changed(self.db_path, cand["candidate_id"],
                                                  vault_root=self.vault)
        self.assertIsNotNone(result)
        self.assertEqual(result["candidate_state"], "pending_review")

    def test_unchanged_candidate_not_invalidated(self):
        """If candidate content hasn't changed, invalidation returns None."""
        from vault_scanner.vault_writer import review_candidate, invalidate_candidate_if_changed
        cand = self._create_candidate()
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        result = invalidate_candidate_if_changed(self.db_path, cand["candidate_id"],
                                                  vault_root=self.vault)
        self.assertIsNone(result)

    # ── 3e: audit events for review actions ─────────────────────────

    def _get_audit_events(self, candidate_id: str) -> list[tuple]:
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            return conn.execute(
                "SELECT event_type, before_state, after_state, reason "
                "FROM audit_events WHERE candidate_id = ? ORDER BY event_id",
                (candidate_id,),
            ).fetchall()

    def test_approve_creates_audit_event(self):
        """Approval creates candidate.reviewed event with before/after states."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        events = self._get_audit_events(cand["candidate_id"])
        reviewed_events = [e for e in events if e[0] == "candidate.reviewed"]
        self.assertEqual(len(reviewed_events), 1)
        self.assertEqual(reviewed_events[0][1], "pending_review")  # before
        self.assertEqual(reviewed_events[0][2], "approved")        # after

    def test_reject_creates_audit_event(self):
        """Rejection creates candidate.rejected event."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="reject", reviewed_by="human-tester")
        events = self._get_audit_events(cand["candidate_id"])
        rejected_events = [e for e in events if e[0] == "candidate.rejected"]
        self.assertEqual(len(rejected_events), 1)

    def test_invalidation_creates_audit_event(self):
        """Content change invalidation creates candidate.review_invalidated."""
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import review_candidate, invalidate_candidate_if_changed
        cand = self._create_candidate()
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(
            file_path.read_text(encoding="utf-8") + "\nMore.\n",
            encoding="utf-8",
        )
        invalidate_candidate_if_changed(self.db_path, cand["candidate_id"],
                                         vault_root=self.vault)
        events = self._get_audit_events(cand["candidate_id"])
        invalidation_events = [e for e in events if e[0] == "candidate.review_invalidated"]
        self.assertEqual(len(invalidation_events), 1)

    # ── 3f: rejected cannot go back to approved without new review ─

    def test_rejected_cannot_be_approved(self):
        """Rejected candidate cannot be approved (no-op)."""
        from vault_scanner.vault_writer import review_candidate
        cand = self._create_candidate()
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="reject", reviewed_by="human-tester")
        # Try to approve
        result = review_candidate(self.db_path, cand["candidate_id"],
                                  decision="approve", reviewed_by="human-tester")
        self.assertEqual(result["candidate_state"], "rejected")


# ── Sliver 6: Atomic Promotion ──────────────────────────────────────


class TestSliver6AtomicPromotion(unittest.TestCase):
    """Atomic promotion: verify, write, state transition, audit, rollback."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.vault = self.temp / "vault"
        self.laos_dir = self.vault / "0-inbox" / "laos-generated"
        self.laos_dir.mkdir(parents=True, exist_ok=True)
        for d in ["1-projects", "2-areas", "3-resources"]:
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp / "test_index.sqlite"
        from vault_scanner.vault_writer import init_candidate_db
        init_candidate_db(self.db_path)

    def _content(self, doc_type: str = "project") -> str:
        return (
            "---\nid: test-sliver6-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_approve_plan(self):
        """Helper: create → approve → plan → return (candidate, plan)."""
        from vault_scanner.vault_writer import create_candidate, review_candidate, plan_promotion
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        return cand, plan

    # ── 6a: Candidate changed after plan creation → fail closed ──────

    def test_promotion_fails_if_candidate_changed(self):
        """Candidate modified between plan creation and promote → reject."""
        from vault_scanner.vault_writer import promote
        cand, plan = self._create_approve_plan()

        # Modify candidate file after plan creation
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(file_path.read_text("utf-8") + "\nTampered.\n", "utf-8")

        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # Candidate state set to 'conflicted' by check_promotion_conflicts
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "conflicted")

        # Candidate file must still exist in laos-generated/
        self.assertTrue(file_path.exists())

        # Target file must NOT exist
        target_full = self.vault / plan["target_path"]
        self.assertFalse(target_full.exists())

        # No promotion.completed audit event
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events WHERE candidate_id = ? "
                "AND event_type = 'promotion.completed'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 0)

        # Candidate file must still exist in laos-generated/
        self.assertTrue(file_path.exists())

        # Target file must NOT exist
        target_full = self.vault / plan["target_path"]
        self.assertFalse(target_full.exists())

        # No promotion.completed audit event
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events WHERE candidate_id = ? "
                "AND event_type = 'promotion.completed'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 0)

    # ── 6b: Target exists → fail closed (never overwrite) ────────────

    def test_promotion_fails_if_target_exists(self):
        """Promotion denied when target path already exists."""
        from vault_scanner.vault_writer import promote
        cand, plan = self._create_approve_plan()

        # Create target file before promotion
        target_full = self.vault / plan["target_path"]
        target_full.write_text("existing content\n", "utf-8")

        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # Target file must be untouched
        self.assertEqual(target_full.read_text("utf-8"), "existing content\n")

        # Candidate state unchanged
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()
        self.assertEqual(row[0], "conflicted")

    # ── 6c: Write failure → no partial file, state unchanged ─────────

    def test_promotion_write_failure_leaves_no_partial(self):
        """If promotion write fails, vault state remains consistent."""
        from vault_scanner.vault_writer import promote
        cand, plan = self._create_approve_plan()

        # Make target directory unwritable by replacing it with a file
        target_parent = (self.vault / plan["target_path"]).parent
        import shutil
        shutil.rmtree(str(target_parent))
        target_parent.write_text("i-am-a-file-not-a-dir\n", "utf-8")

        with self.assertRaises(Exception):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # Candidate file must still exist in laos-generated/
        cand_path = self.vault / cand["relative_path"]
        self.assertTrue(cand_path.exists())

        # No partial target file
        target_full = self.vault / plan["target_path"]
        self.assertFalse(target_full.exists())

        # Candidate state unchanged
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()
        self.assertEqual(row[0], "approved")

        # No promotion.completed audit event
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events WHERE candidate_id = ? "
                "AND event_type = 'promotion.completed'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 0)

    # ── 6d: Successful promotion ─────────────────────────────────────

    def test_successful_promotion(self):
        """Happy path: promote moves file, updates state, writes audit."""
        from vault_scanner.vault_writer import promote
        cand, plan = self._create_approve_plan()
        target_path = plan["target_path"]
        original_content = (self.vault / cand["relative_path"]).read_text("utf-8")

        result = promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # 1. Target file exists with correct content
        target_full = self.vault / target_path
        self.assertTrue(target_full.exists())
        self.assertEqual(target_full.read_text("utf-8"), original_content)

        # 2. Candidate state changed to 'active'
        self.assertEqual(result["candidate_state"], "active")

        # 3. Candidate file removed from laos-generated
        cand_path = self.vault / cand["relative_path"]
        self.assertFalse(cand_path.exists())

        # 4. Plan state is 'completed'
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_row = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        self.assertIsNotNone(plan_row)
        self.assertEqual(plan_row[0], "completed")

        # 5. Audit event written
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            events = conn.execute(
                "SELECT event_type FROM audit_events WHERE candidate_id = ? "
                "AND event_type = 'promotion.completed'",
                (cand["candidate_id"],),
            ).fetchall()
        self.assertEqual(len(events), 1)
