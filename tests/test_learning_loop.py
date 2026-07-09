import argparse
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.learning_loop import LearningLoop, validate_task
from src.memory import db_init, index_store, init_store
from src.memory.candidate import CandidateStore
from src.memory.store import MemoryStore
from src.review.gate import ReviewGate


class ImprovingBackend:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools=None, **options):
        self.calls += 1
        prompt = "\n".join(item["content"] for item in messages)
        ids = re.findall(r"^id: ([^\s]+)$", prompt, flags=re.MULTILINE)
        if ids:
            return {
                "content": (
                    "# Audit\n\nMemory used:\n"
                    + "\n".join(f"- {memory_id}" for memory_id in ids)
                    + "\n\nFindings: idempotency, fail-closed behavior, recovery."
                )
            }
        return {"content": "# Audit\n\nMemory used: none\n\nFinding: idempotency."}


class FailingOnceBackend:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools=None, **options):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated interruption")
        return {"content": "Memory used: none\n\nFinding: safe."}


def task(run_id, target, *, workspace="personal", query=None, criteria=None):
    return {
        "run_id": run_id,
        "task_id": f"audit-{run_id}",
        "type": "repository.module_audit",
        "title": f"Audit {target}",
        "instruction": "Audit the module and cite concrete evidence.",
        "query": query or "idempotency fail-closed recovery module audit",
        "workspace": workspace,
        "confidentiality": "personal" if workspace == "personal" else "internal",
        "inputs": [target],
        "minimum_score": 1.0,
        "criteria": criteria
        or [
            {
                "id": "idempotency",
                "description": "verify idempotency",
                "required_terms": ["idempotency"],
                "strategy": "For module audits, verify idempotency and cite the replay key.",
            },
            {
                "id": "fail_closed",
                "description": "verify fail-closed behavior",
                "required_terms": ["fail-closed"],
                "strategy": (
                    "For module audits, verify fail-closed behavior before treating "
                    "a capability as available."
                ),
            },
            {
                "id": "recovery",
                "description": "verify interruption recovery",
                "required_terms": ["recovery"],
                "strategy": "For module audits, verify recovery after interruption.",
            },
        ],
    }


class LearningLoopTests(unittest.TestCase):
    def test_cli_initializes_empty_data_root(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "learning_loop.py"),
                    "--data-root",
                    data,
                    "--state-dir",
                    state,
                    "--work-root",
                    str(ROOT),
                    "status",
                    "--run-id",
                    "missing-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotIn("请先执行", result.stderr)
            self.assertTrue(Path(data, ".research-agent-root").is_file())

    def test_cli_script_starts_without_pythonpath(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "learning_loop.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.work = tempfile.TemporaryDirectory()
        init_store(self.data.name)
        db_init(argparse.Namespace(root=self.data.name, state_dir=self.state.name))
        index_store(
            argparse.Namespace(
                root=self.data.name,
                state_dir=self.state.name,
                dry_run=False,
            )
        )
        Path(self.work.name, "module_one.py").write_text(
            "def save(key, value):\n    return value\n",
            encoding="utf-8",
        )
        Path(self.work.name, "module_two.py").write_text(
            "def project(event):\n    return event\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.work.cleanup()
        self.state.cleanup()
        self.data.cleanup()

    def test_end_to_end_learning_loop_reuses_only_accepted_memory(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        def first_task():
            t = task("run-one", "module_one.py")
            t["memory_condition"] = "without_memory"
            return t

        first = loop.execute(first_task())
        self.assertEqual(first["state"], "review_pending")
        self.assertEqual(first["score"], 1 / 3)
        self.assertEqual(len(first["candidate_ids"]), 2)
        first_run_path = Path(first["artifacts"]["run.json"])
        first_run_bytes = first_run_path.read_bytes()

        pending_replay = loop.execute(first_task())
        self.assertEqual(pending_replay["candidate_ids"], first["candidate_ids"])
        self.assertEqual(first_run_path.read_bytes(), first_run_bytes)
        self.assertEqual(backend.calls, 1)

        store = MemoryStore(self.data.name)
        self.assertEqual(
            store.active_relevant(
                "fail-closed recovery module audit",
                workspace="personal",
            ),
            [],
        )

        accepted_id, rejected_id = first["candidate_ids"]
        loop.review("run-one", accepted_id, "accept", reason="reusable audit rule")
        reviewed = loop.review("run-one", rejected_id, "reject", reason="too broad")
        self.assertEqual(reviewed["state"], "reviewed")

        active = store.active_relevant(
            "fail-closed module audit",
            workspace="personal",
        )
        self.assertEqual([item["id"] for item in active], [accepted_id])
        self.assertNotEqual(
            MemoryStore(self.data.name).get(rejected_id)["status"],
            "active",
        )

        second_task = task(
            "run-two",
            "module_one.py",
            query="fail-closed idempotency recovery module audit",
        )
        second_task["task_id"] = "audit-run-one"
        second_task["memory_condition"] = "with_memory"
        second_task["baseline_run_id"] = "run-one"
        second = loop.execute(second_task)
        self.assertEqual(second["state"], "completed")
        self.assertEqual(second["score"], 1.0)

        context = json.loads(
            Path(second["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        result = Path(second["artifacts"]["result.md"]).read_text(encoding="utf-8")
        self.assertIn(accepted_id, context["sources"])
        self.assertNotIn(rejected_id, context["sources"])
        self.assertIn(accepted_id, result)
        self.assertEqual(context["restricted_source_count"], 0)

        comparison = loop.compare("run-one", "run-two", minimum_improvement=0.2)
        self.assertTrue(comparison["passed"])
        self.assertGreaterEqual(comparison["score_delta"], 0.2)
        self.assertEqual(comparison["accepted_in_context"], [accepted_id])
        self.assertEqual(comparison["accepted_in_result"], [accepted_id])
        self.assertEqual(comparison["rejected_in_context"], [])
        self.assertEqual(loop.status("run-two")["state"], "verified")

        replay = loop.execute(first_task())
        self.assertEqual(replay["candidate_ids"], first["candidate_ids"])
        self.assertEqual(backend.calls, 2)

        run_dir = Path(self.state.name, "learning_runs", "run-one")
        self.assertTrue((run_dir / "run.json").is_file())
        self.assertTrue((run_dir / "outcome.json").is_file())
        self.assertTrue((run_dir / "reflection.md").is_file())
        self.assertTrue((run_dir / "policy_suggestions.md").is_file())
        self.assertTrue((run_dir / "memory_rules.md").is_file())
        self.assertFalse(any(path.suffix == ".tmp" for path in run_dir.iterdir()))

    def test_nonempty_rejected_and_restricted_matches_are_excluded(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        first_task = task("adversarial-one", "module_one.py", workspace="work")
        first_task["memory_condition"] = "without_memory"
        first = loop.execute(first_task)
        accepted_id, rejected_id = first["candidate_ids"]
        loop.review("adversarial-one", accepted_id, "accept")
        loop.review("adversarial-one", rejected_id, "reject")

        restricted = CandidateStore(self.data.name, self.state.name).create(
            {
                "type": "principle",
                "title": "Restricted fail-closed recovery strategy",
                "scope": "global",
                "workspace": "work",
                "confidentiality": "restricted",
                "source": "test",
                "content": "fail-closed recovery module audit restricted strategy",
                "action": "create",
                "confidence": "confirmed",
                "source_id": "test:restricted:stage07-adversarial",
                "evidence": ["test"],
                "source_refs": ["test:stage07-adversarial"],
                "tags": ["fail-closed", "recovery"],
            }
        )
        restricted_id = restricted["candidate_id"]
        ReviewGate(self.data.name, self.state.name).review(
            "accept", restricted_id, workspace="work"
        )

        second_task = task(
            "adversarial-two",
            "module_one.py",
            workspace="work",
            query="fail-closed idempotency recovery module audit",
        )
        second_task["task_id"] = "audit-adversarial-one"
        second_task["memory_condition"] = "with_memory"
        second_task["baseline_run_id"] = "adversarial-one"
        second = loop.execute(second_task)
        context = json.loads(
            Path(second["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        self.assertIn(accepted_id, context["sources"])
        self.assertNotIn(rejected_id, context["sources"])
        self.assertNotIn(restricted_id, context["sources"])

        comparison = loop.compare(
            "adversarial-one",
            "adversarial-two",
            minimum_improvement=0.2,
            require_nonempty_exclusion_evidence=True,
        )
        self.assertTrue(comparison["passed"])
        self.assertEqual(comparison["rejected_match_ids"], [rejected_id])
        self.assertEqual(comparison["restricted_match_ids"], [restricted_id])
        self.assertEqual(comparison["rejected_in_context"], [])
        self.assertEqual(comparison["restricted_sources"], [])

    def test_interrupted_execution_resumes_without_partial_artifacts(self):
        backend = FailingOnceBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        value = task(
            "recover-run",
            "module_one.py",
            query="safe recovery",
            criteria=[
                {
                    "id": "safe",
                    "description": "report a safe result",
                    "required_terms": ["safe"],
                }
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            loop.execute(value)
        interrupted = loop.status("recover-run")
        self.assertEqual(interrupted["state"], "interrupted")
        self.assertIn("context.json", interrupted["artifacts"])
        self.assertNotIn("result.md", interrupted["artifacts"])

        resumed = loop.execute(value)
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(backend.calls, 2)
        self.assertIn("result.md", resumed["artifacts"])
        self.assertIn("evidence.json", resumed["artifacts"])
        self.assertIn("outcome.json", resumed["artifacts"])

    def test_restricted_memory_is_never_injected(self):
        candidates = CandidateStore(self.data.name, self.state.name)
        candidate = candidates.create(
            {
                "type": "principle",
                "title": "Restricted audit strategy",
                "scope": "global",
                "workspace": "work",
                "confidentiality": "restricted",
                "source": "test",
                "content": "restricted audit strategy secret",
                "action": "create",
                "confidence": "confirmed",
                "source_id": "test:restricted:audit",
                "evidence": ["test"],
                "source_refs": ["test:fixture"],
                "tags": ["restricted", "audit"],
            }
        )
        ReviewGate(self.data.name, self.state.name).review(
            "accept",
            candidate["candidate_id"],
            workspace="work",
        )

        backend = FailingOnceBackend()
        backend.calls = 1
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        value = task(
            "restricted-check",
            "module_one.py",
            workspace="work",
            query="restricted audit strategy secret",
            criteria=[
                {
                    "id": "safe",
                    "description": "report a safe result",
                    "required_terms": ["safe"],
                }
            ],
        )
        result = loop.execute(value)
        context = json.loads(
            Path(result["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        self.assertEqual(context["sources"], [])
        self.assertEqual(context["source_records"], [])
        self.assertEqual(context["restricted_source_count"], 0)

    def test_review_replay_is_idempotent(self):
        loop = LearningLoop(
            self.data.name,
            self.state.name,
            self.work.name,
            ImprovingBackend(),
        )
        first = loop.execute(task("review-replay", "module_one.py"))
        candidate_id = first["candidate_ids"][0]
        accepted = loop.review("review-replay", candidate_id, "accept")
        replay = loop.review("review-replay", candidate_id, "accept")
        self.assertEqual(
            accepted["review_decisions"][candidate_id],
            replay["review_decisions"][candidate_id],
        )
        with self.assertRaisesRegex(ValueError, "different review decision"):
            loop.review("review-replay", candidate_id, "reject")

    def test_compare_rejects_different_input_hashes(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        first = loop.execute(task("input-iso-a", "module_one.py"))
        for candidate_id in first["candidate_ids"]:
            loop.review("input-iso-a", candidate_id, "accept")

        second_task = task(
            "input-iso-b",
            "module_two.py",
        )
        second_task["task_id"] = "audit-input-iso-a"
        second = loop.execute(second_task)
        for candidate_id in second["candidate_ids"]:
            loop.review("input-iso-b", candidate_id, "accept")

        with self.assertRaisesRegex(ValueError, "input"):
            loop.compare("input-iso-a", "input-iso-b")

    def test_compare_rejects_different_workspace(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        first = loop.execute(task("ctx-boundary-ws-a", "module_one.py"))
        for cid in first["candidate_ids"]:
            loop.review("ctx-boundary-ws-a", cid, "accept")

        second_task = task("ctx-boundary-ws-b", "module_one.py", workspace="work")
        second_task["task_id"] = "audit-ctx-boundary-ws-a"
        second = loop.execute(second_task)
        for cid in second["candidate_ids"]:
            loop.review("ctx-boundary-ws-b", cid, "accept")

        with self.assertRaisesRegex(ValueError, "context experiment"):
            loop.compare("ctx-boundary-ws-a", "ctx-boundary-ws-b")

    def test_compare_rejects_different_context_limit(self):
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        first = loop.execute(task("ctx-boundary-cl-a", "module_one.py"))
        for cid in first["candidate_ids"]:
            loop.review("ctx-boundary-cl-a", cid, "accept")

        second_task = task("ctx-boundary-cl-b", "module_one.py")
        second_task["task_id"] = "audit-ctx-boundary-cl-a"
        second_task["context_limit"] = 5000
        second = loop.execute(second_task)
        for cid in second["candidate_ids"]:
            loop.review("ctx-boundary-cl-b", cid, "accept")

        with self.assertRaisesRegex(ValueError, "context experiment"):
            loop.compare("ctx-boundary-cl-a", "ctx-boundary-cl-b")

    def test_prompt_requires_explicit_active_strategy_application(self):
        messages = LearningLoop._prompt(
            task("prompt-check", "module_one.py"),
            {
                "text": "id: principle-1\ncontent: Produce a named evidence map.",
                "sources": ["principle-1"],
            },
            "module source",
        )

        self.assertIn(
            "Apply every supplied active-memory strategy explicitly",
            messages[0]["content"],
        )


    # --- S3.1 RED tests: memory_condition ---

    def test_validate_task_rejects_invalid_memory_condition(self):
        """RED: validate_task should reject invalid memory_condition value."""
        t = task("mc-validate", "module_one.py")
        t["memory_condition"] = "invalid"
        with self.assertRaises(ValueError):
            validate_task(t)

    def test_memory_condition_without_memory_suppresses_context(self):
        """RED: memory_condition='without_memory' should suppress memory injection."""
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        # Create active memory first
        candidates = CandidateStore(self.data.name, self.state.name)
        candidate = candidates.create(
            {
                "type": "principle",
                "title": "Active strategy for mc test",
                "scope": "global",
                "workspace": "personal",
                "confidentiality": "personal",
                "source": "test",
                "content": "suppression test strategy content",
                "action": "create",
                "confidence": "confirmed",
                "source_id": "test:mc:suppress",
                "evidence": ["test"],
                "source_refs": ["test:mc"],
                "tags": ["mc-test"],
            }
        )
        ReviewGate(self.data.name, self.state.name).review(
            "accept", candidate["candidate_id"]
        )

        # Execute with memory_condition=without_memory
        t = task("mc-without", "module_one.py", query="suppression test strategy")
        t["memory_condition"] = "without_memory"
        result = loop.execute(t)
        context = json.loads(
            Path(result["artifacts"]["context.json"]).read_text(encoding="utf-8")
        )
        self.assertEqual(
            context["sources"],
            [],
            "without_memory should suppress all memory injection",
        )

    def test_compare_requires_memory_condition_pair(self):
        """RED: compare() should reject two runs with same memory_condition."""
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        t_a = task("mc-pair-a", "module_one.py")
        t_a["memory_condition"] = "with_memory"
        first = loop.execute(t_a)
        for cid in first["candidate_ids"]:
            loop.review("mc-pair-a", cid, "accept")

        t_b = task("mc-pair-b", "module_one.py")
        t_b["memory_condition"] = "with_memory"
        t_b["task_id"] = t_a["task_id"]
        second = loop.execute(t_b)

        with self.assertRaises(ValueError):
            loop.compare("mc-pair-a", "mc-pair-b")

    # --- S5.1a RED tests: comparison lineage hardening ---

    def test_compare_rejects_different_task_id(self):
        """RED: compare() must reject runs with different task_id outright."""
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        t_a = task("lineage-task-a", "module_one.py")
        t_a["memory_condition"] = "without_memory"
        first = loop.execute(t_a)
        for cid in first["candidate_ids"]:
            loop.review("lineage-task-a", cid, "accept")

        t_b = task("lineage-task-b", "module_one.py")
        t_b["memory_condition"] = "with_memory"
        loop.execute(t_b)

        with self.assertRaisesRegex(ValueError, "task"):
            loop.compare("lineage-task-a", "lineage-task-b")

    def test_comparison_carries_validated_task_id(self):
        """RED: a valid experiment pair must produce a comparison with task_id."""
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)

        t_a = task("lineage-pair-a", "module_one.py")
        t_a["memory_condition"] = "without_memory"
        first = loop.execute(t_a)
        for cid in first["candidate_ids"]:
            loop.review("lineage-pair-a", cid, "accept")

        t_b = task("lineage-pair-b", "module_one.py")
        t_b["task_id"] = t_a["task_id"]
        t_b["memory_condition"] = "with_memory"
        t_b["baseline_run_id"] = "lineage-pair-a"
        loop.execute(t_b)

        comparison = loop.compare("lineage-pair-a", "lineage-pair-b")
        self.assertEqual(comparison["task_id"], t_a["task_id"])

    # --- S3.2 RED tests: evidence composition ---

    def test_memory_evidence_classifies_verified_memory(self):
        """RED: classify_memory_record should return 'verified' for confirmed+active."""
        from src.learning_loop.evidence import classify_memory_record

        record = {
            "id": "memory-verified",
            "confidence": "confirmed",
            "status": "active",
            "superseded_by": [],
        }
        self.assertEqual(classify_memory_record(record), "verified")

    def test_memory_evidence_conflicted_overrides_confirmed(self):
        """RED: classify_memory_record should return 'contradicted' when status is conflicted."""
        from src.learning_loop.evidence import classify_memory_record

        record = {
            "id": "memory-conflicted",
            "confidence": "confirmed",
            "status": "conflicted",
            "superseded_by": [],
        }
        self.assertEqual(classify_memory_record(record), "contradicted")

    def test_memory_evidence_composition_counts_strata(self):
        """RED: build_memory_evidence_composition should count verified/unknown/contradicted."""
        from src.learning_loop.evidence import build_memory_evidence_composition

        records = [
            {
                "id": "a",
                "confidence": "confirmed",
                "status": "active",
                "superseded_by": [],
            },
            {
                "id": "b",
                "confidence": "uncertain",
                "status": "active",
                "superseded_by": [],
            },
            {
                "id": "c",
                "confidence": "confirmed",
                "status": "conflicted",
                "superseded_by": [],
            },
        ]
        result = build_memory_evidence_composition(records)
        self.assertEqual(
            result["summary"],
            {"total": 3, "verified": 1, "unknown": 1, "contradicted": 1},
        )

    def test_memory_evidence_superseded_memory_is_contradicted(self):
        """Contradiction via superseded_by must be classified even when status is active."""
        from src.learning_loop.evidence import classify_memory_record

        record = {
            "id": "memory-old",
            "confidence": "confirmed",
            "status": "active",
            "superseded_by": ["memory-new"],
        }
        self.assertEqual(classify_memory_record(record), "contradicted")

    # --- S3.3 RED tests: utility evaluation ---

    def test_evaluate_pack_utility_uses_score_delta(self):
        """RED: evaluate_pack_utility must return score_delta as pack_utility_delta."""
        from src.learning_loop.evidence import evaluate_pack_utility

        comparison = {"first_score": 0.5, "second_score": 0.8, "score_delta": 0.3}
        result = evaluate_pack_utility(comparison)
        self.assertAlmostEqual(result["pack_utility_delta"], 0.3)
        self.assertAlmostEqual(result["first_score"], 0.5)
        self.assertAlmostEqual(result["second_score"], 0.8)

    def test_evidence_sufficiency_requires_verified_ratio(self):
        """RED: check_evidence_sufficiency must flag insufficient when verified ratio is below threshold."""
        from src.learning_loop.evidence import check_evidence_sufficiency

        composition = {"summary": {"total": 10, "verified": 2, "unknown": 7, "contradicted": 1}}
        result = check_evidence_sufficiency(composition, verified_ratio_min=0.5)
        self.assertEqual(result["status"], "insufficient")
        self.assertAlmostEqual(result["verified_ratio"], 0.2)

    def test_validation_verdict_returns_c_when_evidence_insufficient(self):
        """RED: Verdict C must be returned even when utility_delta is positive, if evidence is insufficient."""
        from src.learning_loop.evidence import generate_validation_verdict

        verdict = generate_validation_verdict(
            utility_delta=0.3,
            evidence_sufficient=False,
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        self.assertEqual(verdict["validation_verdict"], "C")

    def test_validation_verdict_returns_a_for_positive_verified_pack(self):
        """RED: Verdict A for positive delta with sufficient evidence."""
        from src.learning_loop.evidence import generate_validation_verdict

        verdict = generate_validation_verdict(
            utility_delta=0.3,
            evidence_sufficient=True,
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        self.assertEqual(verdict["validation_verdict"], "A")

    def test_utility_evaluation_contains_no_governance_fields(self):
        """RED: build_utility_evaluation must not contain trust/ranking/remove/recommendation."""
        from src.learning_loop.evidence import build_utility_evaluation

        evaluation = build_utility_evaluation(
            experiment={"with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
            composition={"summary": {"total": 5, "verified": 3, "unknown": 1, "contradicted": 1}},
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        text = json.dumps(evaluation).casefold()
        for forbidden in ("trust", "ranking", "remove", "delete", "recommendation"):
            self.assertNotIn(forbidden, text, f"governance field '{forbidden}' must not appear")
        self.assertIn("validation_verdict", evaluation)
        self.assertIn("pack_utility_delta", evaluation["utility"])

    # --- S3.4 RED tests: evidence_sufficiency exposure + md export ---

    def test_utility_evaluation_exposes_evidence_sufficiency(self):
        """RED: build_utility_evaluation must expose evidence_sufficiency in output."""
        from src.learning_loop.evidence import build_utility_evaluation

        evaluation = build_utility_evaluation(
            experiment={"with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
            composition={"summary": {"total": 5, "verified": 3, "unknown": 1, "contradicted": 1}},
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        self.assertIn("evidence_sufficiency", evaluation)
        self.assertIn("status", evaluation["evidence_sufficiency"])
        self.assertIn("verified_ratio", evaluation["evidence_sufficiency"])
        self.assertEqual(evaluation["evidence_sufficiency"]["status"], "sufficient")

    def test_export_utility_evaluation_md_contains_verdict(self):
        """RED: export_utility_evaluation_md must render verdict in markdown."""
        from src.learning_loop.evidence import build_utility_evaluation, export_utility_evaluation_md

        evaluation = build_utility_evaluation(
            experiment={"with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
            composition={"summary": {"total": 5, "verified": 3, "unknown": 1, "contradicted": 1}},
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        md = export_utility_evaluation_md(evaluation)
        self.assertIn("Validation Verdict", md)
        self.assertIn("A", md)

    def test_export_utility_evaluation_md_does_not_add_governance_language(self):
        """RED: md export must not introduce trust/ranking/delete/remove/recommendation."""
        from src.learning_loop.evidence import build_utility_evaluation, export_utility_evaluation_md

        evaluation = build_utility_evaluation(
            experiment={"with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
            composition={"summary": {"total": 5, "verified": 3, "unknown": 1, "contradicted": 1}},
            thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
        )
        md = export_utility_evaluation_md(evaluation).casefold()
        for forbidden in ("trust", "ranking", "remove", "delete", "recommendation"):
            self.assertNotIn(forbidden, md, f"governance term '{forbidden}' must not appear in md export")

    # --- S3.5 RED tests: Independent Evaluation Entry Point ---

    def _valid_bundle(self, **overrides):
        """Return a minimal valid experiment bundle for S3.5 tests."""
        bundle = {
            "experiment": {
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
            },
            "without_memory_outcome": {
                "run_id": "run-a",
                "score": 0.2,
                "used_memory_ids": [],
            },
            "with_memory_outcome": {
                "run_id": "run-b",
                "score": 0.5,
                "used_memory_ids": ["memory-1"],
            },
            "comparison": {
                "first_run_id": "run-a",
                "second_run_id": "run-b",
                "first_score": 0.2,
                "second_score": 0.5,
                "score_delta": 0.3,
            },
            "memory_records": [
                {
                    "id": "memory-1",
                    "confidence": "confirmed",
                    "status": "active",
                    "superseded_by": [],
                },
            ],
            "thresholds": {
                "utility_delta_min": 0.1,
                "verified_ratio_min": 0.5,
                "defined_before_run": True,
            },
        }
        bundle.update(overrides)
        return bundle

    # RED-1 — missing artifact

    def test_evaluate_experiment_bundle_requires_comparison(self):
        """RED: missing comparison must raise EvaluationInputError."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        del bundle["comparison"]
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_evaluate_experiment_bundle_requires_without_memory_outcome(self):
        """RED: missing without_memory_outcome must raise EvaluationInputError."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        del bundle["without_memory_outcome"]
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-2 — run id mismatch

    def test_evaluate_experiment_bundle_rejects_run_id_mismatch(self):
        """RED: experiment run ids must match comparison run ids."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["experiment"]["with_memory_run_id"] = "run-z"
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-3 — score integrity

    def test_evaluate_experiment_bundle_detects_tampered_score_delta(self):
        """RED: tampered score_delta that does not match outcome scores."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["comparison"]["score_delta"] = 0.9
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-4 — without_memory direction

    def test_evaluate_experiment_bundle_rejects_without_memory_using_memory(self):
        """RED: without_memory run must have empty used_memory_ids."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["without_memory_outcome"]["used_memory_ids"] = ["memory-1"]
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-5 — empty memory exposure

    def test_evaluate_experiment_bundle_rejects_with_memory_using_no_memory(self):
        """RED: with_memory run using no memory is an invalid experiment, not Verdict C."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["with_memory_outcome"]["used_memory_ids"] = []
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-6 — threshold validation

    def test_evaluate_experiment_bundle_rejects_invalid_verified_ratio_threshold(self):
        """RED: verified_ratio_min outside [0,1] must be rejected."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["thresholds"]["verified_ratio_min"] = 1.5
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    # RED-7 — valid bundle (capability gap)

    def test_evaluate_experiment_bundle_accepts_valid_bundle(self):
        """RED: valid bundle should not raise — capability does not exist yet."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle

        bundle = self._valid_bundle()
        result = evaluate_experiment_bundle(bundle)
        self.assertIn("validation_verdict", result)
        self.assertIn("pack_utility_delta", result["utility"])
        serialized = json.dumps(result).casefold()
        for forbidden in ("trust", "ranking", "weight", "per_memory_utility", "recommendation"):
            self.assertNotIn(forbidden, serialized, f"'{forbidden}' must not appear in evaluation")
