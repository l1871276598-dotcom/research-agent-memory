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
            experiment={"task_id": "task-1", "with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"task_id": "task-1", "first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
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
            experiment={"task_id": "task-1", "with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"task_id": "task-1", "first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
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
            experiment={"task_id": "task-1", "with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"task_id": "task-1", "first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
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
            experiment={"task_id": "task-1", "with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
            comparison={"task_id": "task-1", "first_score": 0.5, "second_score": 0.8, "score_delta": 0.3},
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
                "task_id": "task-1",
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
                "task_id": "task-1",
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

    # --- S5.1c RED tests: deterministic evaluation identity (v4.2.1 §2.3) ---

    def test_utility_evaluation_is_schema_v2_with_evaluation_id(self):
        """RED: producer must emit schema v2 with eval_<sha256> and experiment.task_id."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle

        result = evaluate_experiment_bundle(self._valid_bundle())
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["experiment"]["task_id"], "task-1")
        self.assertRegex(result["evaluation_id"], r"^eval_[0-9a-f]{64}$")

    def test_evaluation_id_is_deterministic(self):
        """RED: the same semantic bundle must always produce the same ID."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle

        first = evaluate_experiment_bundle(self._valid_bundle())
        second = evaluate_experiment_bundle(self._valid_bundle())
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])

    def test_evaluation_id_changes_with_identity_fields(self):
        """RED: changing any identity field must produce a new ID."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle

        base = evaluate_experiment_bundle(self._valid_bundle())["evaluation_id"]

        tampered = self._valid_bundle()
        tampered["experiment"]["task_id"] = "task-2"
        tampered["comparison"]["task_id"] = "task-2"
        self.assertNotEqual(
            evaluate_experiment_bundle(tampered)["evaluation_id"], base
        )

        rewired = self._valid_bundle()
        rewired["experiment"]["without_memory_run_id"] = "run-c"
        rewired["comparison"]["first_run_id"] = "run-c"
        rewired["without_memory_outcome"]["run_id"] = "run-c"
        self.assertNotEqual(
            evaluate_experiment_bundle(rewired)["evaluation_id"], base
        )

        rescored = self._valid_bundle()
        rescored["without_memory_outcome"]["score"] = 0.1
        rescored["comparison"]["first_score"] = 0.1
        rescored["comparison"]["score_delta"] = 0.4
        self.assertNotEqual(
            evaluate_experiment_bundle(rescored)["evaluation_id"], base
        )

        rethresholded = self._valid_bundle()
        rethresholded["thresholds"]["utility_delta_min"] = 0.2
        self.assertNotEqual(
            evaluate_experiment_bundle(rethresholded)["evaluation_id"], base
        )

        recomposed = self._valid_bundle()
        recomposed["memory_records"].append(
            {
                "id": "memory-2",
                "confidence": "inferred",
                "status": "active",
                "superseded_by": [],
            }
        )
        self.assertNotEqual(
            evaluate_experiment_bundle(recomposed)["evaluation_id"], base
        )

    def test_evaluation_id_ignores_non_identity_variation(self):
        """RED: same aggregate composition and extra threshold keys keep the ID."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle

        base = evaluate_experiment_bundle(self._valid_bundle())["evaluation_id"]

        renamed_memory = self._valid_bundle()
        renamed_memory["memory_records"] = [
            {
                "id": "memory-other",
                "confidence": "confirmed",
                "status": "active",
                "superseded_by": [],
            },
        ]
        self.assertEqual(
            evaluate_experiment_bundle(renamed_memory)["evaluation_id"], base
        )

        extra_threshold = self._valid_bundle()
        extra_threshold["thresholds"]["future_unpromoted_key"] = 42
        self.assertEqual(
            evaluate_experiment_bundle(extra_threshold)["evaluation_id"], base
        )

    # --- S5.1 review-fix RED tests: producer lineage boundary + input identity ---

    def test_build_utility_evaluation_requires_task_lineage(self):
        """RED: the formal v2 producer must reject missing/mismatched task_id."""
        from src.learning_loop.evidence import build_utility_evaluation

        def call(experiment, comparison):
            return build_utility_evaluation(
                experiment=experiment,
                comparison=comparison,
                composition={"summary": {"total": 1, "verified": 1, "unknown": 0, "contradicted": 0}},
                thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
            )

        valid_comparison = {
            "task_id": "task-1",
            "first_score": 0.2,
            "second_score": 0.5,
            "score_delta": 0.3,
        }
        with self.assertRaises(ValueError):
            call({"without_memory_run_id": "run-a", "with_memory_run_id": "run-b"}, valid_comparison)
        with self.assertRaises(ValueError):
            call(
                {"task_id": "", "without_memory_run_id": "run-a", "with_memory_run_id": "run-b"},
                valid_comparison,
            )
        with self.assertRaises(ValueError):
            call(
                {"task_id": "task-1", "without_memory_run_id": "run-a", "with_memory_run_id": "run-b"},
                {"first_score": 0.2, "second_score": 0.5, "score_delta": 0.3},
            )
        with self.assertRaises(ValueError):
            call(
                {"task_id": "task-1", "without_memory_run_id": "run-a", "with_memory_run_id": "run-b"},
                dict(valid_comparison, task_id="task-other"),
            )

    def test_compare_rejects_same_content_under_different_paths(self):
        """RED: input identity is (path, sha256); renamed inputs are a different experiment."""
        backend = ImprovingBackend()
        loop = LearningLoop(self.data.name, self.state.name, self.work.name, backend)
        Path(self.work.name, "module_dup.py").write_text(
            "def save(key, value):\n    return value\n",
            encoding="utf-8",
        )

        t_a = task("path-iso-a", "module_one.py")
        t_a["memory_condition"] = "without_memory"
        first = loop.execute(t_a)
        for cid in first["candidate_ids"]:
            loop.review("path-iso-a", cid, "accept")

        t_b = task("path-iso-b", "module_dup.py")
        t_b["task_id"] = t_a["task_id"]
        t_b["memory_condition"] = "with_memory"
        loop.execute(t_b)

        with self.assertRaisesRegex(ValueError, "input"):
            loop.compare("path-iso-a", "path-iso-b")

    # --- S5.2 RED: reflection builder (baseline v4.3 §4, appendices A/C) ---

    def _valid_enriched(self, **bundle_overrides):
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        bundle = self._valid_bundle(**bundle_overrides)
        return build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))

    def _enriched_with_records(self, extra_records):
        bundle = self._valid_bundle()
        bundle["memory_records"] = bundle["memory_records"] + extra_records
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        return build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))

    def test_reflection_matches_frozen_template_set(self):
        """Acceptance 1: exact template set, order, literals, interpolation."""
        from src.learning_loop.reflection_builder import build_reflection, canonical_json

        enriched = self._valid_enriched()
        snapshot = enriched["source_evaluation_snapshot"]
        result = build_reflection(enriched)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["template_version"], 1)
        self.assertEqual(result["source_evaluation_id"], enriched["source_evaluation_id"])
        self.assertEqual(
            result["outcome_snapshot"],
            {
                "task_id": "task-1",
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
                "validation_verdict": "A",
                "pack_utility_delta": 0.3,
                "evidence_composition": {"verified": 1, "unknown": 0, "contradicted": 0},
                "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 1.0},
            },
        )
        self.assertEqual(
            result["claims"],
            [
                {"claim_id": "CL-1", "statement": "pack_utility_delta is 0.3.",
                 "evidence_ref": "outcome_snapshot.pack_utility_delta"},
                {"claim_id": "CL-2", "statement": 'validation_verdict is "A".',
                 "evidence_ref": "outcome_snapshot.validation_verdict"},
                {"claim_id": "CL-3",
                 "statement": "evidence composition is verified=1, unknown=0, contradicted=0.",
                 "evidence_ref": "outcome_snapshot.evidence_composition"},
                {"claim_id": "CL-4",
                 "statement": 'evidence sufficiency is "sufficient" with verified_ratio 1.0.',
                 "evidence_ref": "outcome_snapshot.evidence_sufficiency"},
                {"claim_id": "CL-5",
                 "statement": "thresholds were utility_delta_min=0.1, verified_ratio_min=0.5, defined_before_run=true.",
                 "evidence_ref": "source_evaluation_snapshot.thresholds"},
            ],
        )
        self.assertEqual(
            result["uncertainties"],
            [
                {"claim_id": "U-3",
                 "statement": "memory records may be stale; staleness_warning is true.",
                 "evidence_ref": "source_evaluation_snapshot.staleness_warning"},
            ],
        )
        self.assertEqual(
            result["missing_information"],
            [
                {"claim_id": "M-1",
                 "statement": "memory records were caller_provided and not re-verified from a run snapshot.",
                 "evidence_ref": "source_evaluation_snapshot.memory_record_source"},
            ],
        )
        self.assertEqual(
            result["non_conclusions"],
            [
                {"claim_id": "N-1",
                 "statement": "pack-level utility delta cannot attribute contribution to any individual memory record.",
                 "contract_ref": "baseline-v4.3 §2"},
                {"claim_id": "N-2",
                 "statement": "the validation verdict does not justify any memory lifecycle action.",
                 "contract_ref": "baseline-v4.3 §2"},
                {"claim_id": "N-3",
                 "statement": "evidence counts do not constitute reliability or weighting of memory records.",
                 "contract_ref": "contracts/06 §3-exclusions"},
                {"claim_id": "N-4",
                 "statement": "coverage between memory_records and used_memory_ids was not checked; no per-memory attribution is provided.",
                 "contract_ref": "contracts/06 §2-not-gates"},
            ],
        )
        self.assertEqual(canonical_json(snapshot), canonical_json(json.loads(canonical_json(snapshot))))

    def test_reflection_is_replay_deterministic(self):
        """Acceptance 2: same input -> identical canonical bytes and rf_ id."""
        from src.learning_loop.reflection_builder import build_reflection, canonical_json

        first = build_reflection(self._valid_enriched())
        second = build_reflection(self._valid_enriched())
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["reflection_id"], second["reflection_id"])

    def test_reflection_distinguishes_snapshot_instances(self):
        """Acceptance 3: same evaluation_id, different snapshot -> new digest and rf_."""
        from src.learning_loop.reflection_builder import build_reflection

        base = build_reflection(self._valid_enriched())

        flipped = self._valid_enriched()
        flipped["source_evaluation_snapshot"]["staleness_warning"] = False
        flipped_result = build_reflection(flipped)

        extra = self._valid_enriched()
        extra["source_evaluation_snapshot"]["thresholds"]["future_unpromoted_key"] = 42
        extra_result = build_reflection(extra)

        for variant in (flipped_result, extra_result):
            self.assertEqual(variant["source_evaluation_id"], base["source_evaluation_id"])
            self.assertNotEqual(variant["source_snapshot_digest"], base["source_snapshot_digest"])
            self.assertNotEqual(variant["reflection_id"], base["reflection_id"])

    def test_reflection_tamper_matrix(self):
        """Acceptance 4: every rejection point independently refuses the input."""
        from src.learning_loop.reflection_builder import build_reflection, ReflectionInputError

        def tampered(mutate):
            enriched = self._valid_enriched()
            mutate(enriched, enriched["source_evaluation_snapshot"])
            return enriched

        def set_consistent(snap, verified, unknown, contradicted, ratio, status, verdict):
            snap["evidence_composition"] = {
                "verified": verified, "unknown": unknown, "contradicted": contradicted,
            }
            snap["evidence_sufficiency"] = {"status": status, "verified_ratio": ratio}
            snap["validation_verdict"] = verdict

        cases = {
            "enriched_not_dict": lambda e, s: None,
            "missing_source_id": lambda e, s: e.pop("source_evaluation_id"),
            "missing_snapshot": lambda e, s: e.pop("source_evaluation_snapshot"),
            "extra_enriched_field": lambda e, s: e.__setitem__("extra", 1),
            "snapshot_not_dict": lambda e, s: e.__setitem__("source_evaluation_snapshot", []),
            "snapshot_unknown_field": lambda e, s: s.__setitem__("surprise", 1),
            "schema_v1": lambda e, s: s.__setitem__("schema_version", 1),
            "schema_str": lambda e, s: s.__setitem__("schema_version", "2"),
            "eval_id_uppercase": lambda e, s: (
                s.__setitem__("evaluation_id", "eval_" + "A" * 64),
                e.__setitem__("source_evaluation_id", "eval_" + "A" * 64),
            ),
            "eval_id_short": lambda e, s: (
                s.__setitem__("evaluation_id", "eval_" + "0" * 63),
                e.__setitem__("source_evaluation_id", "eval_" + "0" * 63),
            ),
            "eval_id_not_str": lambda e, s: (
                s.__setitem__("evaluation_id", 42),
                e.__setitem__("source_evaluation_id", 42),
            ),
            "task_id_empty": lambda e, s: s["experiment"].__setitem__("task_id", ""),
            "run_id_not_str": lambda e, s: s["experiment"].__setitem__("with_memory_run_id", 7),
            "delta_bool": lambda e, s: s["utility"].__setitem__("pack_utility_delta", True),
            "delta_nan": lambda e, s: s["utility"].__setitem__("pack_utility_delta", float("nan")),
            "delta_inf": lambda e, s: s["utility"].__setitem__("pack_utility_delta", float("inf")),
            "delta_str": lambda e, s: s["utility"].__setitem__("pack_utility_delta", "0.3"),
            "count_negative": lambda e, s: s["evidence_composition"].__setitem__("verified", -1),
            "count_bool": lambda e, s: s["evidence_composition"].__setitem__("unknown", False),
            "count_float": lambda e, s: s["evidence_composition"].__setitem__("verified", 1.0),
            "threshold_missing": lambda e, s: s["thresholds"].pop("utility_delta_min"),
            "ratio_min_out_of_range": lambda e, s: s["thresholds"].__setitem__("verified_ratio_min", 1.5),
            "frozen_not_bool": lambda e, s: s["thresholds"].__setitem__("defined_before_run", 1),
            "delta_min_bool": lambda e, s: s["thresholds"].__setitem__("utility_delta_min", True),
            "source_not_str": lambda e, s: s.__setitem__("memory_record_source", 3),
            "staleness_not_bool": lambda e, s: s.__setitem__("staleness_warning", "yes"),
            "status_illegal_enum": lambda e, s: s["evidence_sufficiency"].__setitem__("status", "partial"),
            "verdict_illegal_enum": lambda e, s: s.__setitem__("validation_verdict", "D"),
            "zero_sum_forged_sufficient": lambda e, s: set_consistent(
                s, 0, 0, 0, 0.0, "sufficient", "A"
            ),
            "ratio_tolerance_attack": lambda e, s: set_consistent(
                s, 1, 1, 0, 0.4999999995, "insufficient", "C"
            ),
            "ratio_forged_up": lambda e, s: set_consistent(
                s, 1, 1, 0, 1.0, "sufficient", "A"
            ),
            "verdict_A_at_threshold_boundary": lambda e, s: s["utility"].__setitem__(
                "pack_utility_delta", 0.1
            ),
            "A_with_insufficient": lambda e, s: set_consistent(
                s, 0, 1, 0, 0.0, "insufficient", "A"
            ),
            "B_with_not_frozen": lambda e, s: (
                s["thresholds"].__setitem__("defined_before_run", False),
                s.__setitem__("validation_verdict", "B"),
            ),
            "C_with_sufficient_and_frozen": lambda e, s: s.__setitem__("validation_verdict", "C"),
            "ratio_negative_zero_token": lambda e, s: set_consistent(
                s, 0, 1, 0, -0.0, "insufficient", "C"
            ),
            "ratio_int_zero_token": lambda e, s: set_consistent(
                s, 0, 1, 0, 0, "insufficient", "C"
            ),
            "e1_outer_inner_mismatch": lambda e, s: e.__setitem__(
                "source_evaluation_id", "eval_" + "f" * 64
            ),
            "extra_threshold_nan": lambda e, s: s["thresholds"].__setitem__(
                "future_key", float("nan")
            ),
            "forbidden_extra_threshold_key": lambda e, s: s["thresholds"].__setitem__(
                "trust_boost", 1
            ),
        }
        for label, mutate in cases.items():
            if label == "enriched_not_dict":
                with self.subTest(case=label), self.assertRaises(ReflectionInputError):
                    build_reflection([])
                continue
            with self.subTest(case=label), self.assertRaises(ReflectionInputError):
                build_reflection(tampered(mutate))

    def test_reflection_gate_order_observable(self):
        """Acceptance 5: combined faults report the earliest gate identifier."""
        from src.learning_loop.reflection_builder import build_reflection, ReflectionInputError

        both_shape_and_closure = self._valid_enriched()
        both_shape_and_closure["extra"] = 1
        both_shape_and_closure["source_evaluation_snapshot"]["schema_version"] = 1
        with self.assertRaisesRegex(ReflectionInputError, "gate:shape"):
            build_reflection(both_shape_and_closure)

        both_closure_and_recompute = self._valid_enriched()
        snap = both_closure_and_recompute["source_evaluation_snapshot"]
        snap["evaluation_id"] = "eval_" + "Z" * 64
        both_closure_and_recompute["source_evaluation_id"] = snap["evaluation_id"]
        snap["evidence_sufficiency"]["verified_ratio"] = 0.25
        with self.assertRaisesRegex(ReflectionInputError, "gate:type-closure"):
            build_reflection(both_closure_and_recompute)

        both_recompute_and_e1 = self._valid_enriched()
        snap = both_recompute_and_e1["source_evaluation_snapshot"]
        snap["evidence_sufficiency"]["verified_ratio"] = 0.25
        both_recompute_and_e1["source_evaluation_id"] = "eval_" + "f" * 64
        with self.assertRaisesRegex(ReflectionInputError, "gate:recompute"):
            build_reflection(both_recompute_and_e1)

    def test_reflection_refs_resolve_and_equations_hold(self):
        """Acceptance 6: evidence_ref resolution, contract_ref whitelist, E1-E4."""
        import hashlib

        from src.learning_loop.reflection_builder import build_reflection, canonical_json

        enriched = self._valid_enriched()
        result = build_reflection(enriched)
        snapshot = enriched["source_evaluation_snapshot"]

        def resolve(ref):
            root, _, path = ref.partition(".")
            node = {"outcome_snapshot": result["outcome_snapshot"],
                    "source_evaluation_snapshot": snapshot}[root]
            for part in path.split("."):
                node = node[part]
            return node

        interpolated = {"CL-1", "CL-2", "CL-3", "CL-4", "CL-5", "U-1", "U-2"}
        for entry in result["claims"] + result["uncertainties"] + result["missing_information"]:
            value = resolve(entry["evidence_ref"])
            if entry["claim_id"] in interpolated and not isinstance(value, dict):
                self.assertIn(canonical_json(value), entry["statement"])

        whitelist = {"baseline-v4.3 §2", "contracts/06 §3-exclusions", "contracts/06 §2-not-gates"}
        for entry in result["non_conclusions"]:
            self.assertIn(entry["contract_ref"], whitelist)

        self.assertEqual(enriched["source_evaluation_id"], snapshot["evaluation_id"])
        self.assertEqual(result["source_evaluation_id"], enriched["source_evaluation_id"])
        recomputed = "snap_" + hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
        self.assertEqual(result["source_snapshot_digest"], recomputed)
        projection = result["outcome_snapshot"]
        self.assertEqual(canonical_json(projection["task_id"]), canonical_json(snapshot["experiment"]["task_id"]))
        self.assertEqual(canonical_json(projection["pack_utility_delta"]),
                         canonical_json(snapshot["utility"]["pack_utility_delta"]))
        self.assertEqual(canonical_json(projection["evidence_composition"]),
                         canonical_json(snapshot["evidence_composition"]))
        self.assertEqual(canonical_json(projection["evidence_sufficiency"]),
                         canonical_json(snapshot["evidence_sufficiency"]))

    def test_reflection_ids_match_independent_oracle(self):
        """Acceptance 7: snap_/rf_ recomputed independently, full-length lowercase."""
        import hashlib
        import re

        from src.learning_loop.reflection_builder import build_reflection

        enriched = self._valid_enriched()
        result = build_reflection(enriched)

        canonical = lambda value: json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
        snap_expected = "snap_" + hashlib.sha256(
            canonical(enriched["source_evaluation_snapshot"]).encode("utf-8")
        ).hexdigest()
        rf_payload = {
            "schema_version": 1,
            "template_version": 1,
            "source_evaluation_id": enriched["source_evaluation_id"],
            "source_snapshot_digest": snap_expected,
        }
        rf_expected = "rf_" + hashlib.sha256(canonical(rf_payload).encode("utf-8")).hexdigest()

        self.assertEqual(result["source_snapshot_digest"], snap_expected)
        self.assertEqual(result["reflection_id"], rf_expected)
        self.assertRegex(result["source_snapshot_digest"], r"^snap_[0-9a-f]{64}$")
        self.assertRegex(result["reflection_id"], r"^rf_[0-9a-f]{64}$")

    def test_canonicalizer_golden_vectors(self):
        """Acceptance 8: numeric goldens, escaping, key order, NaN/BOM/newline rules."""
        from src.learning_loop.reflection_builder import canonical_json

        for value, expected in (
            (0.3, "0.3"),
            (0.1 + 0.2, "0.30000000000000004"),
            (1 / 3, "0.3333333333333333"),
            (-0.0, "-0.0"),
            (1e300, "1e+300"),
            (0.5, "0.5"),
            (0, "0"),
            (True, "true"),
            (False, "false"),
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(canonical_json(value), expected)
        self.assertEqual(canonical_json({"任务": "值"}), '{"\\u4efb\\u52a1":"\\u503c"}')
        self.assertEqual(canonical_json({"b": 1, "a": {"d": 2, "c": 3}}), '{"a":{"c":3,"d":2},"b":1}')
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                canonical_json(bad)
        rendered = canonical_json({"a": 1})
        self.assertFalse(rendered.startswith("﻿"))
        self.assertFalse(rendered.endswith("\n"))

    def test_reflection_conditional_templates_iff(self):
        """Acceptance 9: U-1/U-2/U-3/M-1 present iff their condition holds."""
        from src.learning_loop.reflection_builder import build_reflection

        def ids(result, key):
            return [entry["claim_id"] for entry in result[key]]

        base = build_reflection(self._valid_enriched())
        self.assertEqual(ids(base, "uncertainties"), ["U-3"])
        self.assertEqual(ids(base, "missing_information"), ["M-1"])

        with_unknown = build_reflection(self._enriched_with_records([
            {"id": "memory-2", "confidence": "inferred", "status": "active", "superseded_by": []},
        ]))
        self.assertEqual(ids(with_unknown, "uncertainties"), ["U-1", "U-3"])

        with_contradicted = build_reflection(self._enriched_with_records([
            {"id": "memory-3", "confidence": "confirmed", "status": "conflicted", "superseded_by": []},
        ]))
        self.assertEqual(ids(with_contradicted, "uncertainties"), ["U-2", "U-3"])

        with_both = build_reflection(self._enriched_with_records([
            {"id": "memory-2", "confidence": "inferred", "status": "active", "superseded_by": []},
            {"id": "memory-3", "confidence": "confirmed", "status": "conflicted", "superseded_by": []},
        ]))
        self.assertEqual(ids(with_both, "uncertainties"), ["U-1", "U-2", "U-3"])
        self.assertEqual(with_both["outcome_snapshot"]["validation_verdict"], "C")

        stale_off = self._valid_enriched()
        stale_off["source_evaluation_snapshot"]["staleness_warning"] = False
        self.assertEqual(ids(build_reflection(stale_off), "uncertainties"), [])

        other_source = self._valid_enriched()
        other_source["source_evaluation_snapshot"]["memory_record_source"] = "run_snapshot"
        self.assertEqual(ids(build_reflection(other_source), "missing_information"), [])

    def test_reflection_forbidden_scan_domain(self):
        """Acceptance 10: caller values may contain forbidden words; key names may not."""
        from src.learning_loop.reflection_builder import build_reflection

        bundle = self._valid_bundle()
        bundle["experiment"]["task_id"] = "trust-delete-audit-task"
        bundle["comparison"]["task_id"] = "trust-delete-audit-task"
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        enriched = build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))
        result = build_reflection(enriched)
        self.assertEqual(result["outcome_snapshot"]["task_id"], "trust-delete-audit-task")

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        forbidden = ("trust", "ranking", "weight", "per_memory_utility",
                     "recommendation", "remove", "delete")
        for key in keys(result):
            for term in forbidden:
                self.assertNotIn(term, key.casefold())

    def test_reflection_no_filesystem_io(self):
        """Acceptance 11: zero filesystem audit events on success and failure paths."""
        import subprocess
        import sys

        script = r"""
import json, sys
sys.path.insert(0, ".")
sys.path.insert(0, "src")
from src.learning_loop.reflection_builder import build_reflection, ReflectionInputError

snapshot = {
    "schema_version": 2,
    "evaluation_id": "eval_" + "0" * 64,
    "experiment": {"task_id": "t", "without_memory_run_id": "a", "with_memory_run_id": "b"},
    "utility": {"pack_utility_delta": 0.3},
    "evidence_composition": {"verified": 1, "unknown": 0, "contradicted": 0},
    "thresholds": {"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
    "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 1.0},
    "validation_verdict": "A",
    "memory_record_source": "caller_provided",
    "staleness_warning": True,
}
enriched = {"source_evaluation_id": snapshot["evaluation_id"], "source_evaluation_snapshot": snapshot}
bad = {"source_evaluation_id": snapshot["evaluation_id"]}

events = []
def hook(event, args):
    if event == "open" or event.startswith("os.") or event.startswith("shutil."):
        events.append(event)
sys.addaudithook(hook)

build_reflection(json.loads(json.dumps(enriched)))
try:
    build_reflection(bad)
except ReflectionInputError:
    pass
print("EVENTS:" + ",".join(events))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("EVENTS:", completed.stdout)
        self.assertEqual(completed.stdout.strip().split("EVENTS:")[-1], "")

    def test_reflection_no_aliasing(self):
        """Acceptance 12: no shared mutable references in either direction."""
        from src.learning_loop.reflection_builder import build_reflection, canonical_json

        enriched = self._valid_enriched()
        result = build_reflection(enriched)
        result_bytes = canonical_json(result)
        enriched_bytes = canonical_json(enriched)

        enriched["source_evaluation_snapshot"]["experiment"]["task_id"] = "mutated"
        self.assertEqual(canonical_json(result), result_bytes)

        enriched2 = self._valid_enriched()
        result2 = build_reflection(enriched2)
        result2["outcome_snapshot"]["evidence_composition"]["verified"] = 99
        self.assertEqual(canonical_json(enriched2), enriched_bytes)

    # --- S5.1e RED: composition total integrity (baseline v4.3 amendment A2) ---

    def test_build_utility_evaluation_rejects_inconsistent_total(self):
        """RED: summary.total must exist and equal verified+unknown+contradicted."""
        from src.learning_loop.evidence import build_utility_evaluation

        def call(summary):
            return build_utility_evaluation(
                experiment={"task_id": "task-1", "with_memory_run_id": "run-b", "without_memory_run_id": "run-a"},
                comparison={"task_id": "task-1", "first_score": 0.2, "second_score": 0.5, "score_delta": 0.3},
                composition={"summary": summary},
                thresholds={"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
            )

        for label, summary in (
            ("missing_total", {"verified": 1, "unknown": 0, "contradicted": 0}),
            ("mismatched_total", {"total": 5, "verified": 1, "unknown": 0, "contradicted": 0}),
            ("negative_total", {"total": -1, "verified": 0, "unknown": 0, "contradicted": 0}),
            ("bool_total", {"total": True, "verified": 1, "unknown": 0, "contradicted": 0}),
            ("negative_count", {"total": 0, "verified": 1, "unknown": 0, "contradicted": -1}),
        ):
            with self.subTest(case=label), self.assertRaises(ValueError):
                call(summary)

    # --- S5.1 review-fix hardening: contract locks ---

    def test_adapter_rejects_full_score_tamper_matrix(self):
        """Each score field independently rejects missing/bool/NaN/Infinity."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        locations = (
            ("without_memory_outcome", "score"),
            ("with_memory_outcome", "score"),
            ("comparison", "first_score"),
            ("comparison", "second_score"),
            ("comparison", "score_delta"),
        )
        for container, field in locations:
            for label, tamper in (
                ("missing", None),
                ("bool", True),
                ("nan", float("nan")),
                ("inf", float("inf")),
            ):
                bundle = self._valid_bundle()
                if label == "missing":
                    del bundle[container][field]
                else:
                    bundle[container][field] = tamper
                with self.subTest(target=f"{container}.{field}", tamper=label), \
                        self.assertRaises(EvaluationInputError):
                    evaluate_experiment_bundle(bundle)

    def test_evaluation_id_matches_frozen_identity_payload(self):
        """Lock the exact 13-field identity payload and its canonicalization."""
        import hashlib

        from src.learning_loop.evaluation import evaluate_experiment_bundle

        result = evaluate_experiment_bundle(self._valid_bundle())
        payload = {
            "schema_version": 2,
            "experiment": {
                "task_id": "task-1",
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
            },
            "comparison": {
                "first_score": 0.2,
                "second_score": 0.5,
                "score_delta": 0.3,
            },
            "evidence_composition": {
                "verified": 1,
                "unknown": 0,
                "contradicted": 0,
            },
            "thresholds": {
                "utility_delta_min": 0.1,
                "verified_ratio_min": 0.5,
                "defined_before_run": True,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        expected = "eval_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(result["evaluation_id"], expected)

    def test_enrichment_output_has_exactly_two_fields(self):
        """Lock the enriched artifact to the two authorized copy-only fields."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        enriched = build_enriched_utility_evaluation(
            evaluate_experiment_bundle(self._valid_bundle())
        )
        self.assertEqual(
            set(enriched),
            {"source_evaluation_id", "source_evaluation_snapshot"},
        )

    # --- S5.1d RED tests: enrichment snapshot (v4.2.1 §2.4) ---

    def test_enrichment_snapshot_is_copy_only_and_equal_to_input(self):
        """RED: enrichment must copy the evaluation into an immutable snapshot."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        evaluation = evaluate_experiment_bundle(self._valid_bundle())
        enriched = build_enriched_utility_evaluation(evaluation)
        self.assertEqual(enriched["source_evaluation_id"], evaluation["evaluation_id"])
        self.assertEqual(enriched["source_evaluation_snapshot"], evaluation)

        evaluation["validation_verdict"] = "tampered-after-enrichment"
        self.assertNotEqual(enriched["source_evaluation_snapshot"], evaluation)

    def test_enrichment_snapshot_canonical_form_ignores_key_order(self):
        """RED: key insertion order must not affect the canonical snapshot."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        evaluation = evaluate_experiment_bundle(self._valid_bundle())
        reordered = {key: evaluation[key] for key in sorted(evaluation, reverse=True)}
        first = build_enriched_utility_evaluation(evaluation)
        second = build_enriched_utility_evaluation(reordered)
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
        self.assertEqual(
            canonical(first["source_evaluation_snapshot"]),
            canonical(second["source_evaluation_snapshot"]),
        )

    def test_enrichment_rejects_non_finite_values(self):
        """RED: NaN/Infinity anywhere in the evaluation must be rejected."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        evaluation = evaluate_experiment_bundle(self._valid_bundle())
        evaluation["utility"]["pack_utility_delta"] = float("nan")
        with self.assertRaises(ValueError):
            build_enriched_utility_evaluation(evaluation)

    def test_enrichment_rejects_input_without_identity(self):
        """RED: enrichment requires a formal v2 evaluation with evaluation_id."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        evaluation = evaluate_experiment_bundle(self._valid_bundle())
        del evaluation["evaluation_id"]
        with self.assertRaises(ValueError):
            build_enriched_utility_evaluation(evaluation)

    def test_enrichment_adds_no_governance_fields(self):
        """RED: enriched artifact must stay free of governance vocabulary."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation

        evaluation = evaluate_experiment_bundle(self._valid_bundle())
        enriched = build_enriched_utility_evaluation(evaluation)
        serialized = json.dumps(enriched).casefold()
        for forbidden in ("trust", "ranking", "weight", "per_memory_utility", "recommendation"):
            self.assertNotIn(forbidden, serialized, f"'{forbidden}' must not appear in enrichment")

    # --- S5.1b RED tests: adapter integrity (v4.2.1 §2.2) ---

    def test_bundle_rejects_missing_task_id(self):
        """RED: experiment.task_id must be a non-empty string."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        del bundle["experiment"]["task_id"]
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_empty_task_id(self):
        """RED: empty experiment.task_id must be rejected."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["experiment"]["task_id"] = ""
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_task_id_mismatch(self):
        """RED: experiment.task_id must equal comparison.task_id."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["comparison"]["task_id"] = "task-other"
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_without_outcome_run_id_mismatch(self):
        """RED: without_memory_outcome.run_id must equal comparison.first_run_id."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["without_memory_outcome"]["run_id"] = "run-x"
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_with_outcome_run_id_mismatch(self):
        """RED: with_memory_outcome.run_id must equal comparison.second_run_id."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["with_memory_outcome"]["run_id"] = "run-x"
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_missing_outcome_score(self):
        """RED: a missing outcome score must raise even when delta looks consistent."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        del bundle["without_memory_outcome"]["score"]
        bundle["comparison"]["first_score"] = 0.0
        bundle["comparison"]["score_delta"] = 0.5
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_non_finite_score(self):
        """RED: NaN/Infinity scores must be rejected, not slip through delta math."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        for bad in (float("nan"), float("inf")):
            bundle = self._valid_bundle()
            bundle["with_memory_outcome"]["score"] = bad
            bundle["comparison"]["second_score"] = bad
            bundle["comparison"]["score_delta"] = bad
            with self.subTest(bad=bad), self.assertRaises(EvaluationInputError):
                evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_bool_score(self):
        """RED: bool is not a valid score even though it is an int subtype."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["without_memory_outcome"]["score"] = False
        bundle["comparison"]["first_score"] = False
        bundle["comparison"]["score_delta"] = 0.5
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)

    def test_bundle_rejects_comparison_score_tampering(self):
        """RED: comparison first/second_score must equal the outcome scores."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle, EvaluationInputError

        bundle = self._valid_bundle()
        bundle["comparison"]["first_score"] = 0.4
        bundle["comparison"]["second_score"] = 0.7
        with self.assertRaises(EvaluationInputError):
            evaluate_experiment_bundle(bundle)
