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


# ── Sliver 7: Promotion Lock / 并发防护 ─────────────────────────────


class TestSliver7PromotionLock(unittest.TestCase):
    """Phase 3: Concurrent promotion protection via 'promoting' intermediate state.

    The target flow: active → atomically claim to 'promoting' → completed/failed/conflicted.

    Corrected state machine boundaries:
    - completed 不可降级为 failed（历史审计事实）
    - promoting 不能被无 token 的 promote 接管（当前不做 lease）
    - 并发只要求最多一个成功
    """

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
            "---\nid: test-sliver7-note\n"
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

    # ── 7a: 重复执行不能降级 completed 为 failed ─────────────────────

    def test_repeated_promote_rejected_completed_preserved(self):
        """第二次 promote 一个已 completed 的 plan 被拒绝，completed 不变。

        第一次 promote 成功：active → promoting → completed
        第二次 promote：plan 已是 completed，拒绝，但保持 completed 不变。
        completed 是审计事实，不应降级为 failed。

        RED: 当前代码无 claim 步骤，第二次拒绝报 'is completed'。
        Sliver 7 应增加 claim 步骤，使第二次拒绝报 'already_completed'
        或 'plan_not_active'，但 plan 保持 completed。
        """
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import promote
        _, plan = self._create_approve_plan()

        # First promote succeeds
        result = promote(self.db_path, plan["plan_id"], vault_root=self.vault)
        self.assertEqual(result["candidate_state"], "active")

        # Second promote → must fail
        with self.assertRaises(ValueError) as ctx:
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # RED: 当前代码报 'is completed'。
        # Sliver 7 应报 already_completed 或 plan_not_active，
        # 但绝对不能降级 completed → failed。
        err_msg = str(ctx.exception).lower()
        self.assertIn(
            "already_completed", err_msg,
            msg="RED: promote() 应拒绝已 completed 的 plan，"
                f"报 already_completed。Got: {err_msg}"
        )

        # Plan 仍为 completed，不可降级
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        self.assertEqual(state[0], "completed",
                         msg="RED: plan 应保持 completed，"
                             f"实际为 {state[0]}")

        # Target 不变（只有第一次写的文件）
        target_full = self.vault / plan["target_path"]
        self.assertTrue(target_full.exists())

        # promotion.completed 仍只有 1 条
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.completed'",
                (plan["plan_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 1)

    # ── 7b: promoting 状态不可被无 token 的 promote 接管 ────────────

    def test_promoting_not_claimable_by_others(self):
        """plan 在 promoting 状态时，另一个 promote 调用被拒绝。

        不实现 lease/owner token 的情况下：
        - promoting 被拒绝
        - plan 仍保持 promoting（不是 failed — completed 不能降级，
          promoting 也不应被无 token 的 caller 改变状态）
        - 不写 target
        - 不改 candidate

        RED: 当前代码检查 state != 'active' 就拒绝。
        需要的 Sliver 7 行为：
        - promote 内部先原子 claim（UPDATE WHERE state = 'active' → 'promoting'）
        - 第二个 caller 因 claim 0 rows 失败，报 claim_failed
        - plan 保持 promoting（无 owner token 时不应修改他人 claim）
        """
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import promote
        _, plan = self._create_approve_plan()

        # 模拟另一个进程已 claim 该 plan
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = ? WHERE plan_id = ?",
                ("promoting", plan["plan_id"]),
            )
            conn.commit()

        # promote 应因 claim 失败而拒绝
        with self.assertRaises(ValueError) as ctx:
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # RED: 当前代码报 'is promoting'。
        # Sliver 7 应报 claim_failed/plan_not_active。
        err_msg = str(ctx.exception).lower()
        self.assertIn(
            "claim_failed", err_msg,
            msg="RED: claim 失败应报 claim_failed，"
                f"Got: {err_msg}"
        )

        # Plan 保持 promoting（无 lease 时不改为 failed）
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        self.assertEqual(state[0], "promoting",
                         msg="RED: plan 应保持 promoting，"
                             f"Got: {state[0]}。不应降级 completed 为 failed，"
                             "也不应以无 token 方式修改他人的 promoting claim。")

        # Target 未写入
        target_full = self.vault / plan["target_path"]
        self.assertFalse(target_full.exists())

        # Candidate 不变
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            cand_id = conn.execute(
                "SELECT candidate_id FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        if cand_id:
            import sqlite3  # noqa: F811
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                state_row = conn.execute(
                    "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                    (cand_id[0],),
                ).fetchone()
            self.assertIsNotNone(state_row)
            self.assertEqual(state_row[0], "approved")

        # 无 promotion.completed audit
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.completed'",
                (plan["plan_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 0)

    # ── 7c: 并发 promote — 最多一个成功 ──────────────────────────────

    def test_concurrent_promote_at_most_one_succeeds(self):
        """多个线程/进程同时调用 promote(plan_id)，最多一个成功。

        验收标准：
        - 最多 1 次成功
        - plan 最终 completed
        - target 只有 1 个
        - promotion.completed 只有 1 条
        - 失败的调用报 claim_failed 且不影响 completed 状态

        测试方式：使用 threading 模拟两个并发调用。
        不预先设 promoting，让两个 promote 内部竞争 claim。

        RED: 当前代码无 claim 步骤，两个 promote 会同时成功，
        或第二个报 'is completed' 但缺少并发竞争语义。
        """
        import concurrent.futures
        from vault_scanner.vault_writer import promote
        _, plan = self._create_approve_plan()

        def do_promote():
            try:
                result = promote(self.db_path, plan["plan_id"], vault_root=self.vault)
                return {"ok": True, "result": result}
            except ValueError as e:
                return {"ok": False, "error": str(e)}

        # 两个线程同时 promote
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(do_promote)
            fut_b = pool.submit(do_promote)
            results = [fut_a.result(), fut_b.result()]

        # 最多一个成功
        successes = [r for r in results if r["ok"]]
        failures = [r for r in results if not r["ok"]]
        self.assertLessEqual(len(successes), 1,
                             msg=f"最多 1 个 promote 成功，实际 {len(successes)} 个成功")

        # 如果所有都失败，则 RED（至少应有一个成功）
        self.assertGreaterEqual(len(successes), 1,
                                msg="RED: promote 内部需实现 claim 步骤，"
                                    "至少一个 caller 应成功。Got: 全部失败")

        # Plan 最终 completed
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        self.assertEqual(state[0], "completed")

        # Target 只有 1 个
        target_full = self.vault / plan["target_path"]
        self.assertTrue(target_full.exists())
        # 读目标文件内容确认唯一
        content = target_full.read_text(encoding="utf-8")
        self.assertIn("Body.", content)

        # promotion.completed 只有 1 条
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            completed = conn.execute(
                "SELECT event_type FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.completed'",
                (plan["plan_id"],),
            ).fetchall()
        self.assertEqual(len(completed), 1)

        # 失败的调用应接受两种合法 loser 结果：
        # - winner 仍在 promoting → claim_failed
        # - winner 已完成 completed → already_completed
        for f in failures:
            err = f["error"].lower()
            is_claim_failed = "claim_failed" in err
            is_already_completed = "already_completed" in err
            self.assertTrue(
                is_claim_failed or is_already_completed,
                msg=f"失败的 promote 应报 claim_failed 或 already_completed，"
                    f"Got: {f['error']}"
            )


if __name__ == "__main__":
    unittest.main()


# ── Sliver 8: Failure Audit Boundary / 异常审计边界 ─────────────────


class TestSliver8FailureAudit(unittest.TestCase):
    """Phase 3: All promotion failures must leave a queryable audit event.

    Current code (Sliver 7) raises ValueError but does NOT write audit
    events for claim failures (already_completed, claim_failed) or for
    promotion conflicts set to 'conflicted' state.

    These tests are RED: they expect audit events that don't exist yet.
    Sliver 8 should wrap promote() callers with an audit boundary.
    """

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
            "---\nid: test-sliver8-note\n"
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

    def _count_audit_events(self, plan_id: str, event_type: str) -> int:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE plan_id = ? AND event_type = ?",
                (plan_id, event_type),
            ).fetchone()
        return rows[0] if rows else 0

    # ── 8a: already_completed → promotion.failed audit ────────────────

    def test_already_completed_writes_failed_audit(self):
        """已 completed 的 plan 被第二次 promote 后，留下 promotion.failed audit。

        Current code raises ValueError('already_completed') but does NOT
        write an audit event. This test expects exactly 1 promotion.failed
        with reason containing 'already_completed'.

        RED: current code produces 0 audit events for this path.
        """
        from vault_scanner.vault_writer import promote
        _, plan = self._create_approve_plan()

        # First promote succeeds
        promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # Second promote fails
        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # RED: there should be a promotion.failed event, but currently none
        count = self._count_audit_events(plan["plan_id"], "promotion.failed")
        self.assertGreaterEqual(
            count, 1,
            msg="RED: 第二次 promote 已 completed 的 plan 后应有 promotion.failed "
                "audit event，当前为 0。Sliver 8 需在 raise 前写 audit。"
        )

        # Verify the audit event's reason explains it
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT reason FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.failed'",
                (plan["plan_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("already_completed", row[0].lower(),
                      msg="promotion.failed audit 的 reason 应包含 'already_completed'")

    # ── 8b: claim_failed → promotion.failed audit ─────────────────────

    def test_claim_failed_writes_failed_audit(self):
        """promoting 被另一个 promote 拒绝后，留下 promotion.failed audit。

        Current code raises ValueError('claim_failed') but does NOT write
        an audit event. This test expects exactly 1 promotion.failed
        with reason containing 'claim_failed'.

        RED: current code produces 0 audit events for this path.
        """
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import promote
        _, plan = self._create_approve_plan()

        # Manually put plan in 'promoting' (simulating another process)
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = ? WHERE plan_id = ?",
                ("promoting", plan["plan_id"]),
            )
            conn.commit()

        # This promote fails because claim fails
        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # RED: there should be a promotion.failed event, but currently none
        count = self._count_audit_events(plan["plan_id"], "promotion.failed")
        self.assertGreaterEqual(
            count, 1,
            msg="RED: claim 失败后应有 promotion.failed audit event，"
                "当前为 0。Sliver 8 需在 raise 前写 audit。"
        )

        # Verify the audit event's reason explains it
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT reason FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.failed'",
                (plan["plan_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("claim_failed", row[0].lower(),
                      msg="promotion.failed audit 的 reason 应包含 'claim_failed'")

    # ── 8c: candidate hash mismatch → promotion.conflicted audit ──────

    def test_candidate_hash_mismatch_writes_conflicted_audit(self):
        """candidate hash mismatch 后留下 promotion.conflicted audit。

        Current code sets plan.state = 'conflicted' and raises ValueError,
        but does NOT write a promotion.conflicted audit event.
        The conflict reason is already set by check_promotion_conflicts
        which writes 'promotion.conflicted' to audit_events, but only
        via check_promotion_conflicts and only for conflict detection
        called externally — the internal path from promote() may or may
        not produce the expected event.

        RED: promote() does not audit the conflict it raises; the caller
        gets a ValueError with no audit trail.
        """
        from vault_scanner.vault_writer import promote
        cand, plan = self._create_approve_plan()

        # Modify candidate content after planning (causes hash mismatch)
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(
            file_path.read_text(encoding="utf-8") + "\nTampered.\n",
            encoding="utf-8",
        )

        # promote fails due to conflict
        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # The conflict detection inside promote() sets plan to 'conflicted'
        # but does it produce an audit event?

        import sqlite3
        from contextlib import closing

        # Plan should be 'conflicted'
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
        self.assertEqual(state[0], "conflicted",
                         msg="promote 检测到冲突后计划应进入 conflicted 状态")

        # promotion.conflicted 应只有 1 条（由 check_promotion_conflicts 在冲突检测时写入）
        count = self._count_audit_events(plan["plan_id"], "promotion.conflicted")
        self.assertEqual(
            count, 1,
            msg="candidate hash mismatch 后应有且仅有 1 条 promotion.conflicted "
                f"audit event，实际 {count} 条。"
        )

        # Verify the audit event's reason explains it
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT reason FROM audit_events "
                "WHERE plan_id = ? AND event_type = 'promotion.conflicted'",
                (plan["plan_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        # check_promotion_conflicts 写入的 reason 是原始 conflict reasons（如 candidate_plan_hash_mismatch）
        self.assertTrue(
            "hash" in row[0].lower(),
            msg="promotion.conflicted audit 的 reason 应包含 hash 相关信息，"
                f"Got: {row[0]}"
        )



# ── Sliver 9: Recovery Inspection / 恢复前诊断 ─────────────────────


class TestSliver9RecoveryInspection(unittest.TestCase):
    """Phase 3: Recovery must begin with read-only diagnostics.

    RED tests: inspect_promotion_recovery() does not exist yet.
    """

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
            "---\nid: test-sliver9-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_approve_plan(self):
        from vault_scanner.vault_writer import create_candidate, review_candidate, plan_promotion
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        return cand, plan

    # --- 9a: Conflicted hash mismatch ---

    def test_conflicted_hash_mismatch_reports_facts(self):
        """conflicted candidate 返回纯事实：candidate_state, candidate_path_exists, current vs expected hash."""
        from vault_scanner.vault_writer import inspect_promotion_recovery  # noqa
        cand, plan = self._create_approve_plan()
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(
            file_path.read_text(encoding="utf-8") + "\nTampered.\n",
            encoding="utf-8",
        )
        from vault_scanner.vault_writer import check_promotion_conflicts
        conflict = check_promotion_conflicts(
            self.db_path, cand["candidate_id"], self.vault, plan_id=plan["plan_id"]
        )
        self.assertIsNotNone(conflict)
        result = inspect_promotion_recovery(
            self.db_path, plan["plan_id"], vault_root=self.vault
        )

        # Core facts — these are the output of pure inspection
        self.assertEqual(result.get("plan_state"), "active")
        self.assertEqual(result.get("candidate_state"), "conflicted")
        self.assertTrue(result.get("candidate_path_exists"))
        self.assertFalse(result.get("target_path_exists"))
        # Hash mismatch must be observable
        expected = result.get("expected_candidate_hash")
        current = result.get("current_candidate_hash")
        self.assertIsNotNone(expected)
        self.assertIsNotNone(current)
        self.assertNotEqual(expected, current)

        # No decision fields
        self.assertNotIn("safe_to_retry", result)
        self.assertNotIn("recovery_options", result)
        self.assertNotIn("reasons", result)

    # --- 9b: Target already exists ---

    def test_target_exists_reports_facts(self):
        """target 已存在返回纯事实：target_path_exists=True."""
        from vault_scanner.vault_writer import inspect_promotion_recovery  # noqa
        cand, plan = self._create_approve_plan()
        target_path = self.vault / plan["target_path"]
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("Occupied.\n", encoding="utf-8")
        result = inspect_promotion_recovery(
            self.db_path, plan["plan_id"], vault_root=self.vault
        )

        # Core facts
        self.assertEqual(result.get("plan_state"), "active")
        self.assertEqual(result.get("candidate_state"), "approved")
        self.assertTrue(result.get("candidate_path_exists"))
        self.assertTrue(result.get("target_path_exists"))
        self.assertEqual(
            result.get("current_candidate_hash"),
            result.get("expected_candidate_hash"),
        )

        # No decision fields
        self.assertNotIn("safe_to_retry", result)
        self.assertNotIn("recovery_options", result)
        self.assertNotIn("reasons", result)

    # --- 9c: Completed promotion with orphan source ---

    def test_completed_with_orphan_source_reports_facts(self):
        """promotion 完成但 source 残留返回纯事实：candidate_path_exists=True."""
        from vault_scanner.vault_writer import inspect_promotion_recovery, promote  # noqa
        cand, plan = self._create_approve_plan()
        promote(self.db_path, plan["plan_id"], vault_root=self.vault)

        # Source should be gone after successful promote
        source_path = self.vault / cand["relative_path"]
        self.assertFalse(source_path.exists())

        # Re-create source (simulate orphan)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(self._content(), encoding="utf-8")

        result = inspect_promotion_recovery(
            self.db_path, plan["plan_id"], vault_root=self.vault
        )

        # Core facts
        self.assertEqual(result.get("plan_state"), "completed")
        self.assertEqual(result.get("candidate_state"), "active")
        self.assertTrue(result.get("candidate_path_exists"))
        self.assertTrue(result.get("target_path_exists"))

        # No decision fields
        self.assertNotIn("safe_to_retry", result)
        self.assertNotIn("recovery_options", result)
        self.assertNotIn("reasons", result)

    # --- 9d: Inspect is read-only ---

    def test_inspect_is_read_only(self):
        """RED: inspect_promotion_recovery() missing."""
        import sqlite3
        from contextlib import closing
        from vault_scanner.vault_writer import inspect_promotion_recovery  # noqa
        cand, plan = self._create_approve_plan()
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(
            file_path.read_text(encoding="utf-8") + "\nTampered.\n",
            encoding="utf-8",
        )
        from vault_scanner.vault_writer import check_promotion_conflicts
        check_promotion_conflicts(
            self.db_path, cand["candidate_id"], self.vault, plan_id=plan["plan_id"]
        )
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state_before = conn.execute(
                "SELECT candidate_state, state FROM candidates c "
                "JOIN promotion_plans p ON c.candidate_id = p.candidate_id "
                "WHERE p.plan_id = ?", (plan["plan_id"],),
            ).fetchone()
            audit_before = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        target_before = (self.vault / plan["target_path"]).exists()
        source_before = (self.vault / cand["relative_path"]).exists()

        inspect_promotion_recovery(self.db_path, plan["plan_id"], vault_root=self.vault)

        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state_after = conn.execute(
                "SELECT candidate_state, state FROM candidates c "
                "JOIN promotion_plans p ON c.candidate_id = p.candidate_id "
                "WHERE p.plan_id = ?", (plan["plan_id"],),
            ).fetchone()
            audit_after = conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        target_after = (self.vault / plan["target_path"]).exists()
        source_after = (self.vault / cand["relative_path"]).exists()

        self.assertEqual(state_before, state_after)
        self.assertEqual(audit_before, audit_after)
        self.assertEqual(target_before, target_after)
        self.assertEqual(source_before, source_after)





# ── Sliver 10: Recovery Decision / 恢复决策 ─────────────────────


class TestSliver10RecoveryDecision(unittest.TestCase):
    """Phase 3: Decision layer between Inspection (Sliver 9) and Recovery (Sliver 11).

    RED: recommend_recovery_actions() does not exist yet.
    """

    # --- 10a: Conflicted hash mismatch ---

    def test_conflicted_hash_mismatch_blocks_retry(self):
        """RED: recommend_recovery_actions() missing."""
        from vault_scanner.vault_writer import recommend_recovery_actions  # noqa
        facts = {
            "plan_state": "active",
            "candidate_state": "conflicted",
            "candidate_path_exists": True,
            "target_path_exists": False,
            "current_candidate_hash": "abc123",
            "expected_candidate_hash": "def456",
        }
        result = recommend_recovery_actions(facts)
        self.assertIn("re_review_candidate", result.get("allowed_actions", []))
        self.assertIn("abandon_plan", result.get("allowed_actions", []))
        self.assertIn("retry_promotion", result.get("blocked_actions", []))

    # --- 10b: Target exists before completion ---

    def test_target_exists_blocks_retry(self):
        """RED: recommend_recovery_actions() missing."""
        from vault_scanner.vault_writer import recommend_recovery_actions  # noqa
        facts = {
            "plan_state": "active",
            "candidate_state": "approved",
            "candidate_path_exists": True,
            "target_path_exists": True,
            "current_candidate_hash": "abc123",
            "expected_candidate_hash": "abc123",
        }
        result = recommend_recovery_actions(facts)
        self.assertIn("choose_new_target", result.get("allowed_actions", []))
        self.assertIn("abandon_plan", result.get("allowed_actions", []))
        self.assertIn("retry_promotion", result.get("blocked_actions", []))

    # --- 10c: Completed with orphan source ---

    def test_completed_with_orphan_blocks_retry(self):
        """RED: recommend_recovery_actions() missing."""
        from vault_scanner.vault_writer import recommend_recovery_actions  # noqa
        facts = {
            "plan_state": "completed",
            "candidate_state": "active",
            "candidate_path_exists": True,
            "target_path_exists": True,
            "current_candidate_hash": "abc123",
            "expected_candidate_hash": "abc123",
        }
        result = recommend_recovery_actions(facts)
        self.assertIn("cleanup_orphan_source", result.get("allowed_actions", []))
        self.assertIn("retry_promotion", result.get("blocked_actions", []))

    # --- 10d: Healthy active plan ---

    def test_healthy_plan_allows_retry(self):
        """RED: recommend_recovery_actions() missing."""
        from vault_scanner.vault_writer import recommend_recovery_actions  # noqa
        facts = {
            "plan_state": "active",
            "candidate_state": "approved",
            "candidate_path_exists": True,
            "target_path_exists": False,
            "current_candidate_hash": "abc123",
            "expected_candidate_hash": "abc123",
        }
        result = recommend_recovery_actions(facts)
        self.assertIn("retry_promotion", result.get("allowed_actions", []))
        blocked = result.get("blocked_actions", [])
        self.assertNotIn("retry_promotion", blocked)


# ── Sliver 11: Cleanup Orphan Source / 清理孤儿源文件 ─────────────────


class TestSliver11CleanupOrphanSource(unittest.TestCase):
    """Phase 3: Cleanup source candidate file after successful promotion.

    RED: cleanup_orphan_source() does not exist yet.
    """

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
        # Helper re-exports for readability
        self._imports = None

    def _content(self, doc_type: str = "project") -> str:
        return (
            "---\nid: test-sliver11-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_completed_plan(self) -> tuple:
        """Create candidate → approve → plan → force completed + active → return (cand, plan).

        Sets DB states directly to simulate a completed promotion WITHOUT
        running promote(), so the source candidate file still exists.
        """
        from vault_scanner.vault_writer import (
            create_candidate,
            plan_promotion,
            review_candidate,
        )
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = 'completed' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
            conn.execute(
                "UPDATE candidates SET candidate_state = 'active' WHERE candidate_id = ?",
                (cand["candidate_id"],),
            )
            conn.commit()
        return cand, plan

    # --- 11a: Completed plan + source exists → cleaned ---

    def test_completed_plan_cleans_orphan_source(self):
        """RED: cleanup_orphan_source() missing."""
        from vault_scanner.vault_writer import cleanup_orphan_source  # noqa
        cand, plan = self._create_completed_plan()
        source_path = (self.vault / cand["relative_path"]).resolve()
        self.assertTrue(source_path.exists(), "precondition: source exists")
        result = cleanup_orphan_source(self.db_path, plan["plan_id"], self.vault)
        self.assertFalse(source_path.exists(), "source should be deleted")
        self.assertEqual(result.get("status"), "completed")

    # --- 11b: Source already missing → already_clean ---

    def test_source_already_missing_returns_already_clean(self):
        """RED: cleanup_orphan_source() missing."""
        from vault_scanner.vault_writer import cleanup_orphan_source  # noqa
        cand, plan = self._create_completed_plan()
        source_path = (self.vault / cand["relative_path"]).resolve()
        source_path.unlink()
        result = cleanup_orphan_source(self.db_path, plan["plan_id"], self.vault)
        self.assertEqual(result.get("status"), "already_clean")
        # Plan/candidate state unchanged
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
            cand_state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "completed")
        self.assertEqual(cand_state, "active")

    # --- 11c: Source path outside laos-generated → fail closed ---

    def test_source_outside_laos_generated_fails_closed(self):
        """RED: cleanup_orphan_source() missing."""
        from vault_scanner.vault_writer import cleanup_orphan_source  # noqa
        cand, plan = self._create_completed_plan()
        # Place a note outside laos-generated
        external_path = self.vault / "1-projects" / "user-note.md"
        external_path.write_text("# User note\n", "utf-8")
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE candidates SET relative_path = ? WHERE candidate_id = ?",
                ("1-projects/user-note.md", cand["candidate_id"]),
            )
            conn.commit()
        with self.assertRaises(ValueError):
            cleanup_orphan_source(self.db_path, plan["plan_id"], self.vault)
        self.assertTrue(external_path.exists(), "external file must not be deleted")

    # --- 11d: Plan not completed → fail closed ---

    def test_non_completed_plan_fails_closed(self):
        """RED: cleanup_orphan_source() missing."""
        from vault_scanner.vault_writer import cleanup_orphan_source  # noqa
        from vault_scanner.vault_writer import (
            create_candidate,
            plan_promotion,
            review_candidate,
        )
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        source_path = (self.vault / cand["relative_path"]).resolve()
        self.assertTrue(source_path.exists(), "precondition: source exists")
        self.assertEqual(plan["plan_state"], "planned", "precondition: plan is not completed")
        with self.assertRaises(ValueError):
            cleanup_orphan_source(self.db_path, plan["plan_id"], self.vault)
        self.assertTrue(source_path.exists(), "source must not be deleted")

    # --- 11e: Path traversal in DB → fail closed ---

    def test_db_path_traversal_fails_closed(self):
        """RED: cleanup_orphan_source() missing."""
        from vault_scanner.vault_writer import cleanup_orphan_source  # noqa
        cand, plan = self._create_completed_plan()
        # Place a user note outside laos-generated
        user_note = self.vault / "1-projects" / "user-note.md"
        user_note.parent.mkdir(parents=True, exist_ok=True)
        user_note.write_text("# User note\n", "utf-8")
        # Tamper relative_path with .. traversal that string-prefix would allow
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE candidates SET relative_path = ? WHERE candidate_id = ?",
                ("0-inbox/laos-generated/../../1-projects/user-note.md",
                 cand["candidate_id"]),
            )
            conn.commit()
        with self.assertRaises(ValueError):
            cleanup_orphan_source(self.db_path, plan["plan_id"], self.vault)
        self.assertTrue(user_note.exists(), "external file must not be deleted")


# ── Sliver 12: Abandon Promotion Plan / 放弃晋升计划 ─────────────────


class TestSliver12AbandonPromotionPlan(unittest.TestCase):
    """Phase 3: Abandon a promotion plan that should no longer proceed.

    RED: abandon_promotion_plan() does not exist yet.
    """

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
            "---\nid: test-sliver12-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_active_plan(self) -> tuple:
        """Create candidate → approve → plan → return (cand, plan)."""
        from vault_scanner.vault_writer import (
            create_candidate,
            plan_promotion,
            review_candidate,
        )
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        return cand, plan

    def _count_audit_events(self, plan_id: str, event_type: str) -> int:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE plan_id = ? AND event_type = ?",
                (plan_id, event_type),
            ).fetchone()
        return rows[0] if rows else 0

    # --- 12a: Active plan can be abandoned ---

    def test_active_plan_can_be_abandoned(self):
        """RED: abandon_promotion_plan() missing."""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        result = abandon_promotion_plan(
            self.db_path, plan["plan_id"],
            actor="tester", reason="not needed",
        )
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
            cand_state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "abandoned")
        self.assertEqual(cand_state, "approved")
        self.assertGreaterEqual(
            self._count_audit_events(plan["plan_id"], "promotion.abandoned"), 1,
        )

    # --- 12b: Conflicted plan can be abandoned ---

    def test_conflicted_plan_can_be_abandoned(self):
        """RED: abandon_promotion_plan() missing."""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = 'conflicted' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
            conn.commit()
        result = abandon_promotion_plan(
            self.db_path, plan["plan_id"],
            actor="tester", reason="conflicts unresolvable",
        )
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "abandoned")

    # --- 12c: Failed plan can be abandoned ---

    def test_failed_plan_can_be_abandoned(self):
        """RED: abandon_promotion_plan() missing."""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = 'failed' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
            conn.commit()
        result = abandon_promotion_plan(
            self.db_path, plan["plan_id"],
            actor="tester", reason="clean up after failure",
        )
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "abandoned")

    # --- 12d: Completed plan cannot be abandoned ---

    def test_completed_plan_cannot_be_abandoned(self):
        """RED: abandon_promotion_plan() missing."""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = 'completed' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
            conn.commit()
        with self.assertRaises(ValueError):
            abandon_promotion_plan(
                self.db_path, plan["plan_id"],
                actor="tester", reason="should not be allowed",
            )
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "completed")

    # --- 12f: Promoting plan cannot be abandoned ---

    def test_promoting_plan_cannot_be_abandoned(self):
        """promoting 状态的 plan 不能被 abandon，状态保持不变。"""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE promotion_plans SET state = 'promoting' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
            conn.commit()
        with self.assertRaises(ValueError):
            abandon_promotion_plan(
                self.db_path, plan["plan_id"],
                actor="tester", reason="cannot abandon promoting",
            )
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]
        self.assertEqual(plan_state, "promoting")

    # --- 12g: Missing plan_id cannot be abandoned ---

    def test_missing_plan_cannot_be_abandoned(self):
        """不存在的 plan_id 调用 abandon 应 raise ValueError，不留 audit。"""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        with self.assertRaises(ValueError):
            abandon_promotion_plan(
                self.db_path, "missing-plan-id",
                actor="tester", reason="should not exist",
            )
        # 不应有 promotion.abandoned audit
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            abandoned = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type = 'promotion.abandoned'",
            ).fetchone()[0]
        self.assertEqual(abandoned, 0)

    # --- 12e: Abandoned plan cannot be promoted ---

    def test_abandoned_plan_cannot_be_promoted(self):
        """RED: abandon_promotion_plan() missing."""
        from vault_scanner.vault_writer import abandon_promotion_plan  # noqa
        cand, plan = self._create_active_plan()
        abandon_promotion_plan(
            self.db_path, plan["plan_id"],
            actor="tester", reason="superseded",
        )
        from vault_scanner.vault_writer import promote
        with self.assertRaises(ValueError):
            promote(self.db_path, plan["plan_id"], vault_root=self.vault)
        # Target must not be written
        target_path = self.vault / plan["target_path"]
        self.assertFalse(target_path.exists(), "promote must not write target")
        # Candidate state unchanged
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            cand_state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(cand_state, "approved")


# ── Sliver 13: Re-review Conflicted Candidate ──────────────────────


class TestSliver13ReReviewConflictedCandidate(unittest.TestCase):
    """Phase 3: Re-review a conflicted candidate — send back to pending_review.

    RED: re_review_conflicted_candidate() does not exist yet.
    """

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
            "---\nid: test-sliver13-note\n"
            "schema_version: 1\n"
            f"type: {doc_type}\nlifecycle: active\nsource: laos\n"
            "created: 2026-07-07\nupdated: 2026-07-07\n---\nBody.\n"
        )

    def _create_conflicted_candidate(self) -> tuple:
        """Create → approve → plan → tamper candidate → conflict detection → return (cand, plan)."""
        from vault_scanner.vault_writer import (
            create_candidate, review_candidate, plan_promotion,
            check_promotion_conflicts,
        )
        content = self._content()
        cand = create_candidate(vault_root=self.vault, db_path=self.db_path,
                                content=content, generator="test-suite",
                                generator_version="0.1.0")
        review_candidate(self.db_path, cand["candidate_id"],
                         decision="approve", reviewed_by="human-tester")
        plan = plan_promotion(self.db_path, cand["candidate_id"], self.vault)
        # Tamper candidate to force conflict
        file_path = self.vault / cand["relative_path"]
        file_path.write_text(file_path.read_text("utf-8") + "\nTampered.\n", "utf-8")
        check_promotion_conflicts(self.db_path, cand["candidate_id"],
                                   self.vault, plan_id=plan["plan_id"])
        return cand, plan

    def _count_audit_events(self, candidate_id: str, event_type: str) -> int:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE candidate_id = ? AND event_type = ?",
                (candidate_id, event_type),
            ).fetchone()
        return rows[0] if rows else 0

    # --- 13a: Conflicted candidate → re-reviewed → pending_review ---

    def test_conflicted_can_be_re_reviewed(self):
        """RED: re_review_conflicted_candidate() missing."""
        from vault_scanner.vault_writer import re_review_conflicted_candidate  # noqa
        cand, plan = self._create_conflicted_candidate()

        result = re_review_conflicted_candidate(
            self.db_path, cand["candidate_id"],
            actor="tester", reason="updated",
        )

        self.assertEqual(result["candidate_state"], "pending_review")

        # reviewed_hash, reviewed_at, reviewed_by cleared
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            row = conn.execute(
                "SELECT reviewed_hash, reviewed_at, reviewed_by "
                "FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()
        self.assertIsNone(row[0])  # reviewed_hash cleared
        self.assertIsNone(row[1])  # reviewed_at cleared
        self.assertIsNone(row[2])  # reviewed_by cleared

        # audit event
        self.assertGreaterEqual(
            self._count_audit_events(cand["candidate_id"],
                                      "candidate.re_review_requested"), 1,
        )

    # --- 13b: Approved candidate cannot be re-reviewed ---

    def test_approved_cannot_be_re_reviewed(self):
        """RED: re_review_conflicted_candidate() missing."""
        from vault_scanner.vault_writer import re_review_conflicted_candidate  # noqa
        from vault_scanner.vault_writer import review_candidate
        cand, plan = self._create_conflicted_candidate()

        # Directly set candidate_state back to approved (simulate)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE candidates SET candidate_state = 'approved' "
                "WHERE candidate_id = ?",
                (cand["candidate_id"],),
            )
            conn.commit()

        with self.assertRaises(ValueError):
            re_review_conflicted_candidate(
                self.db_path, cand["candidate_id"],
                actor="tester", reason="should not be allowed",
            )

        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(state, "approved")

    # --- 13c: Active candidate cannot be re-reviewed ---

    def test_active_cannot_be_re_reviewed(self):
        """RED: re_review_conflicted_candidate() missing."""
        from vault_scanner.vault_writer import re_review_conflicted_candidate  # noqa
        cand, plan = self._create_conflicted_candidate()

        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            conn.execute(
                "UPDATE candidates SET candidate_state = 'active' "
                "WHERE candidate_id = ?",
                (cand["candidate_id"],),
            )
            conn.commit()

        with self.assertRaises(ValueError):
            re_review_conflicted_candidate(
                self.db_path, cand["candidate_id"],
                actor="tester", reason="should not be allowed",
            )

        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(state, "active")

    # --- 13d: Old promotion plan is NOT revived ---

    def test_re_review_does_not_revive_old_plan(self):
        """Plan state is NEVER modified by re_review_conflicted_candidate.

        check_promotion_conflicts() returns early when candidate_state != 'approved',
        so the plan stays 'active' (never set to 'conflicted'). After re-review,
        the plan must remain unchanged — re-review touches only the candidate.
        """
        from vault_scanner.vault_writer import re_review_conflicted_candidate  # noqa
        cand, plan = self._create_conflicted_candidate()

        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state_before = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]

        re_review_conflicted_candidate(
            self.db_path, cand["candidate_id"],
            actor="tester", reason="updated",
        )

        with closing(sqlite3.connect(str(self.db_path))) as conn:
            plan_state_after = conn.execute(
                "SELECT state FROM promotion_plans WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()[0]

        # Plan state must never change — re-review only touches candidates table
        self.assertEqual(plan_state_before, plan_state_after,
                         msg="Re-review must not modify promotion_plans table")
        # Plan must not be 'active' when it was 'conflicted' or 'failed' before
        # (though in this scenario check_promotion_conflicts returns early,
        #  so the plan may still be 'active' — the key invariant is NO change)

    # --- 13e: Candidate file missing → fail closed ---

    def test_missing_file_fails_closed(self):
        """RED: re_review_conflicted_candidate() missing."""
        from vault_scanner.vault_writer import re_review_conflicted_candidate  # noqa
        cand, plan = self._create_conflicted_candidate()

        # Delete the candidate file
        file_path = self.vault / cand["relative_path"]
        file_path.unlink()
        self.assertFalse(file_path.exists(), "precondition: candidate file removed")

        with self.assertRaises(ValueError):
            re_review_conflicted_candidate(
                self.db_path, cand["candidate_id"],
                actor="tester", reason="file lost",
                vault_root=self.vault,
            )

        # Candidate state still conflicted
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(self.db_path))) as conn:
            state = conn.execute(
                "SELECT candidate_state FROM candidates WHERE candidate_id = ?",
                (cand["candidate_id"],),
            ).fetchone()[0]
        self.assertEqual(state, "conflicted")


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
