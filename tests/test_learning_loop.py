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


class S53EligibilityTests(unittest.TestCase):
    """S5.3 Eligibility + FSM Matrix v1 — RED/GREEN TDD."""

    # --- S5.3 Task 1: RED-0 scaffold ---

    def test_eligibility_imports(self):
        """RED-0: build_eligibility and EligibilityInputError are importable."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        self.assertTrue(callable(build_eligibility))
        self.assertTrue(issubclass(EligibilityInputError, ValueError))

    def test_eligibility_not_yet_fully_implemented(self):
        """RED-0: build_eligibility without valid keys fails shape gate, not NotImplementedError."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility({}, {})
        self.assertEqual(ctx.exception.gate, "shape")

    def test_eligibility_input_error_has_gate_and_check(self):
        """RED-0: EligibilityInputError carries structured gate/check attributes."""
        from src.learning_loop.eligibility import EligibilityInputError
        err = EligibilityInputError(gate="shape", message="test")
        self.assertEqual(err.gate, "shape")
        self.assertIsNone(err.check)
        err2 = EligibilityInputError(gate="preconditions", check="P3", message="fail")
        self.assertEqual(err2.gate, "preconditions")
        self.assertEqual(err2.check, "P3")

    def test_canonical_json_can_now_serialize(self):
        """RED-0: _canonical_json is now implemented and serializes dicts."""
        from src.learning_loop.eligibility import _canonical_json
        result = _canonical_json({"a": 1})
        self.assertEqual(result, '{"a":1}')

    # --- S5.3 Task 2: RED-1 FSM matrix ---

    def test_fsm_matrix_is_exactly_12_rows(self):
        """RED-1: FSM_MATRIX_V1 must contain exactly 12 rows."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        self.assertEqual(len(FSM_MATRIX_V1), 12)

    def test_fsm_matrix_has_case_01_through_case_12(self):
        """RED-1: case IDs must be case_01..case_12."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        ids = [row.case_id for row in FSM_MATRIX_V1]
        expected = [f"case_{i:02d}" for i in range(1, 13)]
        self.assertEqual(ids, expected)

    def test_fsm_matrix_covers_all_combinations(self):
        """RED-1: (verdict, contradicted_gt0, unknown_gt0) covers complete 3×2×2."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        keys = {(row.verdict, row.contradicted_gt0, row.unknown_gt0) for row in FSM_MATRIX_V1}
        expected = set()
        for v in ("A", "B", "C"):
            for c in (False, True):
                for u in (False, True):
                    expected.add((v, c, u))
        self.assertEqual(keys, expected)

    def test_fsm_matrix_no_duplicate_keys(self):
        """RED-1: no two rows share the same (verdict, contradicted_gt0, unknown_gt0)."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        keys = [(row.verdict, row.contradicted_gt0, row.unknown_gt0) for row in FSM_MATRIX_V1]
        self.assertEqual(len(keys), len(set(keys)))

    def test_fsm_matrix_a_b_eligible_c_ineligible(self):
        """RED-1: A/B rows must be eligible, C rows ineligible."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        for row in FSM_MATRIX_V1:
            if row.verdict in ("A", "B"):
                self.assertEqual(row.status, "eligible",
                                 f"{row.case_id} should be eligible")
            else:
                self.assertEqual(row.status, "ineligible",
                                 f"{row.case_id} should be ineligible")

    def test_fsm_matrix_embedded_12_row_table_exact(self):
        """RED-1: exact embedded 12-row table matches v4.3 §5.3."""
        from src.learning_loop.eligibility import FSM_MATRIX_V1
        expected = (
            ("case_01", "A", False, False, "eligible"),
            ("case_02", "A", False, True, "eligible"),
            ("case_03", "A", True, False, "eligible"),
            ("case_04", "A", True, True, "eligible"),
            ("case_05", "B", False, False, "eligible"),
            ("case_06", "B", False, True, "eligible"),
            ("case_07", "B", True, False, "eligible"),
            ("case_08", "B", True, True, "eligible"),
            ("case_09", "C", False, False, "ineligible"),
            ("case_10", "C", False, True, "ineligible"),
            ("case_11", "C", True, False, "ineligible"),
            ("case_12", "C", True, True, "ineligible"),
        )
        for i, (cid, v, c, u, s) in enumerate(expected):
            row = FSM_MATRIX_V1[i]
            self.assertEqual(row.case_id, cid, f"row {i} case_id")
            self.assertEqual(row.verdict, v, f"row {i} verdict")
            self.assertEqual(row.contradicted_gt0, c, f"row {i} contradicted_gt0")
            self.assertEqual(row.unknown_gt0, u, f"row {i} unknown_gt0")
            self.assertEqual(row.status, s, f"row {i} status")

    # --- S5.3 Task 3: RED-2 canonicalizer ---

    def test_canonical_golden_numeric_vectors(self):
        """RED-2: numeric golden vectors all produce expected canonical tokens."""
        from src.learning_loop.eligibility import _canonical_json
        for value, expected in (
            (0.3, "0.3"),
            (0.30000000000000004, "0.30000000000000004"),
            (1 / 3, "0.3333333333333333"),
            (-0.0, "-0.0"),
            (1e300, "1e+300"),
            (0.5, "0.5"),
            (0, "0"),
            (True, "true"),
            (False, "false"),
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(_canonical_json(value), expected)

    def test_canonical_chinese_escaped(self):
        """RED-2: non-ASCII characters are escaped."""
        from src.learning_loop.eligibility import _canonical_json
        self.assertEqual(
            _canonical_json({"任务": "值"}),
            '{"\\u4efb\\u52a1":"\\u503c"}'
        )

    def test_canonical_key_sorting(self):
        """RED-2: nested dicts are sorted by key."""
        from src.learning_loop.eligibility import _canonical_json
        self.assertEqual(
            _canonical_json({"b": 1, "a": {"d": 2, "c": 3}}),
            '{"a":{"c":3,"d":2},"b":1}'
        )

    def test_canonical_rejects_nan_infinity(self):
        """RED-2: NaN, Infinity, -Infinity raise ValueError."""
        from src.learning_loop.eligibility import _canonical_json
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                _canonical_json(bad)

    def test_canonical_no_bom_no_trailing_newline(self):
        """RED-2: output must not start with BOM or end with newline."""
        from src.learning_loop.eligibility import _canonical_json
        rendered = _canonical_json({"a": 1})
        self.assertFalse(rendered.startswith("﻿"))
        self.assertFalse(rendered.endswith("\n"))

    # --- S5.3 Task 4: RED-3 gate:shape ---

    def _reflection_valid(self):
        return {
            "schema_version": 1,
            "template_version": 1,
            "reflection_id": "rf_" + "a" * 64,
            "source_evaluation_id": "eval_" + "b" * 64,
            "source_snapshot_digest": "snap_" + "c" * 64,
            "outcome_snapshot": {
                "task_id": "task-1",
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
                "validation_verdict": "A",
                "pack_utility_delta": 0.3,
                "evidence_composition": {"verified": 1, "unknown": 0, "contradicted": 0},
                "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 1.0},
            },
            "claims": [],
            "uncertainties": [],
            "missing_information": [],
            "non_conclusions": [],
        }

    def _enriched_valid(self):
        return {
            "source_evaluation_id": "eval_" + "b" * 64,
            "source_evaluation_snapshot": {"schema_version": 2},
        }

    def test_shape_reflection_not_dict(self):
        """RED-3: non-dict reflection must raise EligibilityInputError gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility([], self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_enriched_not_dict(self):
        """RED-3: non-dict enriched must raise EligibilityInputError gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), [])
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_reflection_missing_top_field(self):
        """RED-3: missing reflection top field -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        del ref["reflection_id"]
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_reflection_extra_top_field(self):
        """RED-3: extra reflection top field -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["extra_field"] = True
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_enriched_missing_field(self):
        """RED-3: missing enriched field -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid()
        del enr["source_evaluation_snapshot"]
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_enriched_extra_field(self):
        """RED-3: extra enriched field -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid()
        enr["extra"] = 1
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_outcome_snapshot_not_dict(self):
        """RED-3: outcome_snapshot not dict -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["outcome_snapshot"] = []
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_composition_not_dict(self):
        """RED-3: evidence_composition not dict -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["outcome_snapshot"]["evidence_composition"] = 3
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_sufficiency_not_dict(self):
        """RED-3: evidence_sufficiency not dict -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["outcome_snapshot"]["evidence_sufficiency"] = "insufficient"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_claims_not_list(self):
        """RED-3: claims not a list -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["claims"] = {}
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_uncertainties_not_list(self):
        """RED-3: uncertainties not a list -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["uncertainties"] = None
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, self._enriched_valid())
        self.assertEqual(ctx.exception.gate, "shape")

    def test_shape_snapshot_not_dict(self):
        """RED-3: source_evaluation_snapshot not dict -> gate=shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid()
        enr["source_evaluation_snapshot"] = "not-a-dict"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "shape")

    # --- S5.3 Task 5: RED-4 gate:source ---

    def _enriched_valid_for_source(self):
        """Return a minimal enriched dict valid for S5.2 build_reflection."""
        return {
            "source_evaluation_id": "eval_" + "d" * 64,
            "source_evaluation_snapshot": {
                "schema_version": 2,
                "evaluation_id": "eval_" + "d" * 64,
                "experiment": {
                    "task_id": "task-1",
                    "without_memory_run_id": "run-a",
                    "with_memory_run_id": "run-b",
                },
                "utility": {"pack_utility_delta": 0.3},
                "evidence_composition": {"verified": 1, "unknown": 0, "contradicted": 0},
                "thresholds": {
                    "utility_delta_min": 0.1,
                    "verified_ratio_min": 0.5,
                    "defined_before_run": True,
                },
                "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 1.0},
                "validation_verdict": "A",
                "memory_record_source": "run_snapshot",
                "staleness_warning": False,
            },
        }

    def _full_producer_enriched(self, **bundle_overrides):
        """Return enriched built via real Phase 4 chains: evaluation -> enrichment."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation
        bundle = self._valid_bundle(**bundle_overrides)
        return build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))

    def _full_producer_eligibility(self, **bundle_overrides):
        """Return eligibility artifact through full producer chain."""
        from src.learning_loop.reflection_builder import build_reflection
        from src.learning_loop.eligibility import build_eligibility
        enr = self._full_producer_enriched(**bundle_overrides)
        ref = build_reflection(enr)
        return build_eligibility(ref, enr)

    def _valid_bundle(self, **overrides):
        """Return a minimal valid experiment bundle for S3.5 tests."""
        bundle = {
            "experiment": {
                "task_id": "task-full-producer",
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
                "task_id": "task-full-producer",
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

    def test_source_tampered_schema_version(self):
        """RED-4: non-v2 schema_version -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["schema_version"] = 1
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_tampered_eval_id(self):
        """RED-4: illegal evaluation_id -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["evaluation_id"] = "bad_id"
        enr["source_evaluation_id"] = "bad_id"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_empty_task_id(self):
        """RED-4: empty task_id -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["experiment"]["task_id"] = ""
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_delta_bool(self):
        """RED-4: bool pack_utility_delta -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["utility"]["pack_utility_delta"] = True
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_delta_nan(self):
        """RED-4: NaN delta -> gate=source."""
        import math
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["utility"]["pack_utility_delta"] = math.nan
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_count_negative(self):
        """RED-4: negative composition count -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["evidence_composition"]["verified"] = -1
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_count_bool(self):
        """RED-4: bool composition count -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["evidence_composition"]["unknown"] = False
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_ratio_insufficient_false_delta(self):
        """RED-4: inconsistent ratio/status/verdict -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        snap = enr["source_evaluation_snapshot"]
        snap["evidence_composition"] = {"verified": 0, "unknown": 1, "contradicted": 0}
        snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
        snap["validation_verdict"] = "A"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_e1_mismatch(self):
        """RED-4: source_evaluation_id != snapshot.evaluation_id -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_id"] = "eval_" + "f" * 64
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_extra_snapshot_field(self):
        """RED-4: unknown snapshot field -> gate=source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["surprise"] = "extra"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(self._reflection_valid(), enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_source_legit_enriched_does_not_fail_with_valid_reflection(self):
        """RED-4: valid enriched should not fail source gate."""
        from src.learning_loop.eligibility import build_eligibility
        enr = self._enriched_valid_for_source()
        ref = self._reflection_valid()
        ref["source_evaluation_id"] = enr["source_evaluation_id"]
        try:
            build_eligibility(ref, enr)
        except Exception as exc:
            # Must not fail at source gate; other gates may reject (e.g. upstream comparison)
            self.assertNotEqual(getattr(exc, "gate", None), "source")

    # --- S5.3 Task 6: RED-5 gate:preconditions P1-P8 ---

    def _reflection_with_enriched(self, enr=None):
        """Return (reflection, enriched) tuple valid through gate:shape and gate:source."""
        from src.learning_loop.reflection_builder import build_reflection
        if enr is None:
            enr = self._enriched_valid_for_source()
        ref = build_reflection(enr)
        return ref, enr

    def test_preconditions_P1_illegal_verdict(self):
        """RED-5: illegal verdict -> gate=preconditions check=P1."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["validation_verdict"] = "D"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P1")

    def test_preconditions_P1_illegal_status(self):
        """RED-5: illegal status -> gate=preconditions check=P1."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["evidence_sufficiency"]["status"] = "partial"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P1")

    def test_preconditions_P2_negative_count(self):
        """RED-5: negative count -> gate=preconditions check=P2."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["evidence_composition"]["verified"] = -1
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P2")

    def test_preconditions_P2_bool_count(self):
        """RED-5: bool count -> gate=preconditions check=P2."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["evidence_composition"]["unknown"] = False
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P2")

    def test_preconditions_P3_ratio_mismatch(self):
        """RED-5: ratio != verified/total -> gate=preconditions check=P3."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["evidence_sufficiency"]["verified_ratio"] = 0.99
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P3")

    def test_preconditions_P3_total_zero_but_sufficient(self):
        """RED-5: total=0 but status=sufficient -> gate=preconditions check=P3."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        comp = ref["outcome_snapshot"]["evidence_composition"]
        comp["verified"] = 0
        comp["unknown"] = 0
        comp["contradicted"] = 0
        ref["outcome_snapshot"]["evidence_sufficiency"]["verified_ratio"] = 0.0
        ref["outcome_snapshot"]["evidence_sufficiency"]["status"] = "sufficient"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P3")

    def test_preconditions_P4_A_with_insufficient(self):
        """RED-5: verdict=A with insufficient -> gate=preconditions check=P4."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["evidence_composition"]["verified"] = 0
        ref["outcome_snapshot"]["evidence_composition"]["unknown"] = 1
        ref["outcome_snapshot"]["evidence_composition"]["contradicted"] = 0
        ref["outcome_snapshot"]["evidence_sufficiency"] = {
            "status": "insufficient", "verified_ratio": 0.0,
        }
        ref["outcome_snapshot"]["validation_verdict"] = "A"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P4")

    def test_preconditions_P5_A_with_delta_at_min(self):
        """RED-5: verdict=A, delta==min -> gate=preconditions check=P5."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = 0.1
        ref["outcome_snapshot"]["validation_verdict"] = "A"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P5")

    def test_preconditions_P6_B_with_delta_above_min(self):
        """RED-5: verdict=B, delta>min -> gate=preconditions check=P6."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = 0.3
        ref["outcome_snapshot"]["validation_verdict"] = "B"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P6")

    def test_preconditions_P7_C_with_frozen_and_sufficient(self):
        """RED-5: verdict=C, frozen+sufficient -> gate=preconditions check=P7."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        snap = enr["source_evaluation_snapshot"]
        self.assertTrue(snap["thresholds"]["defined_before_run"])
        ref["outcome_snapshot"]["validation_verdict"] = "C"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P7")

    def test_preconditions_delta_bool(self):
        """RED-5: bool delta -> gate=preconditions check=P5."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = True
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P5")

    def test_preconditions_delta_str(self):
        """RED-5: string delta -> gate=preconditions check=P5."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = "0.3"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P5")

    def test_preconditions_delta_nan(self):
        """RED-5: NaN delta -> gate=preconditions check=P5."""
        import math
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = math.nan
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P5")

    def test_preconditions_delta_inf(self):
        """RED-5: Infinity delta -> gate=preconditions check=P5."""
        import math
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["pack_utility_delta"] = math.inf
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P5")

    def test_preconditions_P8_task_id_mismatch(self):
        """RED-5: outcome_snapshot.task_id != source task_id -> gate=preconditions check=P8."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["task_id"] = "different-task"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")
        self.assertEqual(ctx.exception.check, "P8")

    # --- S5.3 Task 7: RED-6 gate:upstream ---

    def test_upstream_tampered_schema_version(self):
        """RED-6: altered schema_version -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["schema_version"] = 2
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_claim_statement(self):
        """RED-6: altered claim text -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["claims"][0]["statement"] = "TAMPERED"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_claim_order(self):
        """RED-6: reordered claims -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["claims"] = list(reversed(ref["claims"]))
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_reflection_id(self):
        """RED-6: altered reflection_id -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["reflection_id"] = "rf_" + "e" * 64
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_snapshot_digest(self):
        """RED-6: altered source_snapshot_digest -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["source_snapshot_digest"] = "snap_" + "f" * 64
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_evidence_ref(self):
        """RED-6: altered evidence_ref -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["claims"][0]["evidence_ref"] = "tampered.path"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_tampered_contract_ref(self):
        """RED-6: altered contract_ref -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["non_conclusions"][0]["contract_ref"] = "fake-contract"
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_extra_claim(self):
        """RED-6: extra claim entry -> gate=upstream."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["claims"].append({"claim_id": "CL-6", "statement": "EXTRA"})
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "upstream")

    # --- S5.3 Task 7 cont'd: RED-7 gate order combination ---

    def test_gate_order_shape_before_source(self):
        """RED-7: shape+source fault -> earliest is shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["extra"] = True  # shape fail
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["schema_version"] = 1  # source fail
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "shape")

    def test_gate_order_source_before_preconditions(self):
        """RED-7: source+preconditions fault -> earliest is source."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = self._reflection_valid()
        ref["outcome_snapshot"]["validation_verdict"] = "D"  # P1 fail
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["schema_version"] = 1  # source fail
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "source")

    def test_gate_order_preconditions_before_upstream(self):
        """RED-7: preconditions+upstream fault -> earliest is preconditions."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref, enr = self._reflection_with_enriched()
        ref["outcome_snapshot"]["validation_verdict"] = "D"  # P1 fail
        ref["claims"][0]["statement"] = "tampered"  # upstream fail too
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "preconditions")

    def test_gate_order_all_four_together(self):
        """RED-7: all four gates fail -> earliest is shape."""
        from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
        ref = []
        enr = self._enriched_valid_for_source()
        enr["source_evaluation_snapshot"]["schema_version"] = 1
        with self.assertRaises(EligibilityInputError) as ctx:
            build_eligibility(ref, enr)
        self.assertEqual(ctx.exception.gate, "shape")

    # --- S5.3 Task 7 cont'd: RED-8 FSM lookup ---

    def _valid_pair_through_gates(self):
        """Build a (reflection, enriched) pair valid through all four gates."""
        from src.learning_loop.reflection_builder import build_reflection
        enr = self._enriched_valid_for_source()
        ref = build_reflection(enr)
        return ref, enr

    def test_fsm_lookup_returns_artifact_with_required_fields(self):
        """RED-8: valid input produces artifact with seven mandatory keys."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        expected_keys = {
            "schema_version", "eligibility_id", "source_reflection_id",
            "fsm_matrix_version", "matched_case", "eligibility_status", "reasons",
        }
        self.assertEqual(set(result), expected_keys)

    def test_fsm_lookup_verdict_A_matches_correct_case(self):
        """RED-8: verdict A with contradicted=0, unknown=0 -> case_01."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        self.assertEqual(result["matched_case"], "case_01")
        self.assertEqual(result["eligibility_status"], "eligible")

    def test_fsm_lookup_verdict_C_matches_correct_case(self):
        """RED-8: verdict C with contradicted=1, unknown=0 -> case_11."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 0, "unknown": 0, "contradicted": 1}
        snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
        snap["validation_verdict"] = "C"
        snap["utility"]["pack_utility_delta"] = -0.1
        snap["thresholds"]["defined_before_run"] = True
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["matched_case"], "case_11")
        self.assertEqual(result["eligibility_status"], "ineligible")

    # --- S5.3 Task 8: RED-9 reasons ---

    def test_reasons_A_no_unknown_no_contradicted(self):
        """RED-9: verdict A, no unknowns, no contradictions -> single verdict A reason."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict A: pack_utility_delta exceeds the frozen utility_delta_min; disposition review is available.",
        ])

    def test_reasons_A_with_unknown(self):
        """RED-9: verdict A with unknowns -> [R-A, R-U]."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 1, "unknown": 1, "contradicted": 0}
        snap["evidence_sufficiency"] = {"status": "sufficient", "verified_ratio": 0.5}
        snap["validation_verdict"] = "A"
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict A: pack_utility_delta exceeds the frozen utility_delta_min; disposition review is available.",
            "unknown records present: unknown=1.",
        ])

    def test_reasons_B_with_contradicted(self):
        """RED-9: verdict B with contradictions -> [R-B, R-X]."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        # B requires delta<=min, sufficient, frozen. Contradicted=1, but ratio remains >=min.
        snap["evidence_composition"] = {"verified": 6, "unknown": 0, "contradicted": 1}
        snap["evidence_sufficiency"] = {"status": "sufficient", "verified_ratio": 6 / 7}
        snap["validation_verdict"] = "B"
        snap["utility"]["pack_utility_delta"] = 0.05
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict B: pack_utility_delta does not exceed the frozen utility_delta_min; disposition review is available.",
            "contradicted records present: contradicted=1.",
        ])

    def test_reasons_C_both_not_frozen_and_insufficient(self):
        """RED-9: C with both not-frozen and insufficient -> [R-C1, R-C2, R-U, R-X]."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 0, "unknown": 1, "contradicted": 1}
        snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
        snap["validation_verdict"] = "C"
        snap["utility"]["pack_utility_delta"] = -0.1
        snap["thresholds"]["defined_before_run"] = False
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict C: thresholds were not frozen before the run (defined_before_run is false).",
            "verdict C: evidence sufficiency is insufficient.",
            "unknown records present: unknown=1.",
            "contradicted records present: contradicted=1.",
        ])

    def test_reasons_C_only_not_frozen_sufficient(self):
        """RED-9: C only not-frozen, still sufficient -> [R-C1]."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 1, "unknown": 0, "contradicted": 0}
        snap["evidence_sufficiency"] = {"status": "sufficient", "verified_ratio": 1.0}
        snap["validation_verdict"] = "C"
        snap["thresholds"]["defined_before_run"] = False
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict C: thresholds were not frozen before the run (defined_before_run is false).",
        ])

    def test_reasons_C_only_insufficient_frozen(self):
        """RED-9: C only insufficient, frozen -> [R-C2]."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 0, "unknown": 0, "contradicted": 0}
        snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
        snap["validation_verdict"] = "C"
        snap["utility"]["pack_utility_delta"] = -0.1
        snap["thresholds"]["defined_before_run"] = True
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["reasons"], [
            "verdict C: evidence sufficiency is insufficient.",
        ])

    # --- S5.3 Task 8: RED-10 eligibility ID ---

    def test_eligibility_id_full_length_format(self):
        """RED-10: eligibility_id matches ^elig_[0-9a-f]{64}$."""
        import re
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        self.assertRegex(result["eligibility_id"], r"^elig_[0-9a-f]{64}$")

    def test_eligibility_id_is_deterministic(self):
        """RED-10: same input -> same eligibility_id."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        first = build_eligibility(ref, enr)["eligibility_id"]
        second = build_eligibility(ref, enr)["eligibility_id"]
        self.assertEqual(first, second)

    def test_eligibility_id_changes_with_reflection_id(self):
        """RED-10: different source_reflection_id -> different eligibility_id."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        first_id = build_eligibility(ref, enr)["eligibility_id"]
        # Use a different evaluating pair that naturally produces a different reflection_id
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["experiment"]["task_id"] = "task-2"
        snap["evaluation_id"] = "eval_" + "e" * 64
        enr2 = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref2 = build_reflection(enr2)
        second_id = build_eligibility(ref2, enr2)["eligibility_id"]
        self.assertNotEqual(first_id, second_id)

    def test_eligibility_id_independent_oracle(self):
        """RED-10: independent re-computation of elig_ matches production."""
        import hashlib
        from src.learning_loop.eligibility import build_eligibility, _canonical_json
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        # Independent oracle
        payload = {
            "schema_version": 1,
            "fsm_matrix_version": 1,
            "source_reflection_id": ref["reflection_id"],
        }
        expected = "elig_" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(result["eligibility_id"], expected)

    # --- S5.3 Task 8: RED-11 12-case eligibility ---

    def _case_fixture(self, verdict, contradicted, unknown, delta=0.3):
        """Build pair through full chain for a given FSM combination."""
        import copy
        snap = copy.deepcopy(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["memory_record_source"] = "run_snapshot"
        snap["staleness_warning"] = False

        if verdict in ("A", "B"):
            verified = 5 + unknown + contradicted  # high enough for ratio >= 0.5
            total = verified + unknown + contradicted
            ratio = verified / total
            snap["evidence_composition"] = {
                "verified": verified, "unknown": unknown, "contradicted": contradicted,
            }
            snap["evidence_sufficiency"] = {"status": "sufficient", "verified_ratio": ratio}
            snap["validation_verdict"] = verdict
            snap["utility"]["pack_utility_delta"] = delta if verdict == "A" else 0.05
        else:
            # C — make it insufficient (defined_before_run stays True)
            if unknown + contradicted == 0:
                # total=0 case: all counts zero, ratio=0.0, status=insufficient
                snap["evidence_composition"] = {"verified": 0, "unknown": 0, "contradicted": 0}
                snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
            else:
                snap["evidence_composition"] = {
                    "verified": 0, "unknown": unknown, "contradicted": contradicted,
                }
                snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
            snap["validation_verdict"] = "C"
            snap["utility"]["pack_utility_delta"] = -0.1

        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        return ref, enr

    def test_12_case_contract_layer_all_cases(self):
        """RED-11: 12 frozen fixtures each produce expected case, status, reasons."""
        from src.learning_loop.eligibility import build_eligibility

        R_A = "verdict A: pack_utility_delta exceeds the frozen utility_delta_min; disposition review is available."
        R_B = "verdict B: pack_utility_delta does not exceed the frozen utility_delta_min; disposition review is available."
        R_C2 = "verdict C: evidence sufficiency is insufficient."
        R_U1 = "unknown records present: unknown=1."
        R_X1 = "contradicted records present: contradicted=1."

        cases = [
            ("case_01", "A", 0, 0, "eligible", [R_A]),
            ("case_02", "A", 0, 1, "eligible", [R_A, R_U1]),
            ("case_03", "A", 1, 0, "eligible", [R_A, R_X1]),
            ("case_04", "A", 1, 1, "eligible", [R_A, R_U1, R_X1]),
            ("case_05", "B", 0, 0, "eligible", [R_B]),
            ("case_06", "B", 0, 1, "eligible", [R_B, R_U1]),
            ("case_07", "B", 1, 0, "eligible", [R_B, R_X1]),
            ("case_08", "B", 1, 1, "eligible", [R_B, R_U1, R_X1]),
            ("case_09", "C", 0, 0, "ineligible", [R_C2]),
            ("case_10", "C", 0, 1, "ineligible", [R_C2, R_U1]),
            ("case_11", "C", 1, 0, "ineligible", [R_C2, R_X1]),
            ("case_12", "C", 1, 1, "ineligible", [R_C2, R_U1, R_X1]),
        ]
        for expected_case, verdict, c, u, expected_status, expected_reasons in cases:
            ref, enr = self._case_fixture(verdict, int(c), int(u))
            result = build_eligibility(ref, enr)
            with self.subTest(case=expected_case):
                self.assertEqual(result["matched_case"], expected_case,
                                 f"{expected_case}: wrong matched_case")
                self.assertEqual(result["eligibility_status"], expected_status,
                                 f"{expected_case}: wrong status")
                self.assertEqual(result["fsm_matrix_version"], 1,
                                 f"{expected_case}: wrong fsm_version")
                self.assertEqual(result["schema_version"], 1,
                                 f"{expected_case}: wrong schema_version")
                self.assertEqual(result["reasons"], expected_reasons,
                                 f"{expected_case}: wrong reasons")
                self.assertEqual(result["source_reflection_id"], ref["reflection_id"],
                                 f"{expected_case}: wrong reflection_id")

    def test_eligibility_replay_deterministic(self):
        """RED-11: same input -> same bytes and ID across calls."""
        from src.learning_loop.eligibility import build_eligibility, _canonical_json
        ref, enr = self._valid_pair_through_gates()
        first = build_eligibility(ref, enr)
        second = build_eligibility(ref, enr)
        self.assertEqual(_canonical_json(first), _canonical_json(second))
        self.assertEqual(first["eligibility_id"], second["eligibility_id"])

    # --- S5.3 Task 8: RED-12 cross-cutting invariants ---

    def test_ineligible_still_produces_artifact(self):
        """RED-12: C class still produces full artifact with reasons."""
        from src.learning_loop.eligibility import build_eligibility
        snap = dict(self._enriched_valid_for_source()["source_evaluation_snapshot"])
        snap["evidence_composition"] = {"verified": 0, "unknown": 1, "contradicted": 0}
        snap["evidence_sufficiency"] = {"status": "insufficient", "verified_ratio": 0.0}
        snap["validation_verdict"] = "C"
        snap["utility"]["pack_utility_delta"] = -0.1
        snap["thresholds"]["defined_before_run"] = True
        enr = {"source_evaluation_id": snap["evaluation_id"], "source_evaluation_snapshot": snap}
        from src.learning_loop.reflection_builder import build_reflection
        ref = build_reflection(enr)
        result = build_eligibility(ref, enr)
        self.assertEqual(result["eligibility_status"], "ineligible")
        self.assertIn("reasons", result)
        self.assertEqual(result["matched_case"], "case_10")

    def test_no_aliasing_forward(self):
        """RED-12: modifying input after call does not affect artifact."""
        from src.learning_loop.eligibility import build_eligibility, _canonical_json
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        result_bytes = _canonical_json(result)
        ref["schema_version"] = 99
        self.assertEqual(_canonical_json(result), result_bytes)

    def test_no_aliasing_reverse(self):
        """RED-12: modifying artifact output does not affect input."""
        from src.learning_loop.eligibility import build_eligibility, _canonical_json
        ref, enr = self._valid_pair_through_gates()
        input_bytes = _canonical_json(ref)
        result = build_eligibility(ref, enr)
        result["eligibility_status"] = "tampered"
        self.assertEqual(_canonical_json(ref), input_bytes)

    def test_output_key_scan_no_forbidden_terms(self):
        """RED-12: output keys are free of governance vocabulary."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        forbidden = ("trust", "ranking", "weight", "per_memory_utility",
                     "recommendation", "remove", "delete")
        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)
        for key in keys(result):
            for term in forbidden:
                self.assertNotIn(term, key.casefold())

    def test_output_field_count_exactly_seven(self):
        """RED-12: eligibility output has exactly the seven frozen keys."""
        from src.learning_loop.eligibility import build_eligibility
        ref, enr = self._valid_pair_through_gates()
        result = build_eligibility(ref, enr)
        self.assertEqual(
            set(result),
            {"schema_version", "eligibility_id", "source_reflection_id",
             "fsm_matrix_version", "matched_case", "eligibility_status", "reasons"},
        )

    # --- S5.3 cross-cutting: no-I/O audit (fresh process) ---

    def test_eligibility_no_filesystem_io(self):
        """RED-12: zero filesystem audit events on success AND all 4 failure gates."""
        import subprocess
        import sys

        script = r"""
import copy, json, sys
sys.path.insert(0, ".")
sys.path.insert(0, "src")

from src.learning_loop.eligibility import build_eligibility, EligibilityInputError

snapshot = {
    "schema_version": 2,
    "evaluation_id": "eval_" + "d" * 64,
    "experiment": {"task_id": "t", "without_memory_run_id": "a", "with_memory_run_id": "b"},
    "utility": {"pack_utility_delta": 0.3},
    "evidence_composition": {"verified": 1, "unknown": 0, "contradicted": 0},
    "thresholds": {"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
    "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 1.0},
    "validation_verdict": "A",
    "memory_record_source": "run_snapshot",
    "staleness_warning": False,
}

enr = {"source_evaluation_id": snapshot["evaluation_id"], "source_evaluation_snapshot": snapshot}

from src.learning_loop.reflection_builder import build_reflection
ref = build_reflection(enr)

events = []
def hook(event, args):
    if event == "open" or event.startswith("os.") or event.startswith("shutil."):
        events.append(event)
sys.addaudithook(hook)

def check_gate(label, expected_gate, fn):
    try:
        fn()
    except EligibilityInputError as e:
        if e.gate != expected_gate:
            print(f"FAIL: {label} expected gate={expected_gate!r}, got {e.gate!r}")
            sys.exit(1)
    except Exception as e:
        print(f"FAIL: {label} raised {type(e).__name__}: {e}")
        sys.exit(1)
    else:
        print(f"FAIL: {label} did not raise")
        sys.exit(1)

# 1 — success
result = build_eligibility(ref, enr)
assert "eligibility_id" in result

# 2 — shape: non-dict reflection
check_gate("shape", "shape", lambda: build_eligibility([], enr))

# 3 — source: passes shape, fails S5.2 producer
bad_source = copy.deepcopy(enr)
bad_source["source_evaluation_snapshot"]["schema_version"] = 1
check_gate("source", "source", lambda: build_eligibility(ref, bad_source))

# 4 — preconditions: passes shape+source, tamper verdict to "D"
bad_pre = copy.deepcopy(ref)
bad_pre["outcome_snapshot"]["validation_verdict"] = "D"
check_gate("preconditions", "preconditions", lambda: build_eligibility(bad_pre, enr))

# 5 — upstream: passes all earlier gates, tamper claim statement
bad_up = copy.deepcopy(ref)
bad_up["claims"] = copy.deepcopy(ref["claims"])
bad_up["claims"][0] = dict(ref["claims"][0])
bad_up["claims"][0]["statement"] = "tampered claim"
check_gate("upstream", "upstream", lambda: build_eligibility(bad_up, enr))

print("EVENTS:" + ",".join(events))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(completed.returncode, 0, f"stderr: {completed.stderr}")
        self.assertIn("EVENTS:", completed.stdout)
        self.assertEqual(completed.stdout.strip().split("EVENTS:")[-1], "",
                         f"IO events detected: {completed.stdout}")

    # --- S5.3 cross-cutting: full producer integration ---

    def test_full_producer_A(self):
        """RED-11: full producer chain produces verdict A artifact."""
        result = self._full_producer_eligibility()
        self.assertEqual(result["eligibility_status"], "eligible")
        self.assertEqual(result["matched_case"], "case_01")

    def test_full_producer_B(self):
        """RED-11: full producer chain with lower delta produces verdict B."""
        bundle = dict(self._valid_bundle())
        bundle["comparison"]["score_delta"] = 0.0
        bundle["comparison"]["first_score"] = 0.5
        bundle["comparison"]["second_score"] = 0.5
        bundle["without_memory_outcome"]["score"] = 0.5
        bundle["with_memory_outcome"]["score"] = 0.5
        bundle["thresholds"]["utility_delta_min"] = 0.1
        result = self._full_producer_eligibility(**bundle)
        self.assertEqual(result["eligibility_status"], "eligible")
        self.assertEqual(result["matched_case"], "case_05")

    def test_full_producer_C1_only(self):
        """RED-11: C1-only via defined_before_run=False."""
        bundle = dict(self._valid_bundle())
        bundle["thresholds"]["defined_before_run"] = False
        result = self._full_producer_eligibility(**bundle)
        self.assertEqual(result["eligibility_status"], "ineligible")
        # C1 only: not-frozen, but still sufficient
        self.assertIn("thresholds were not frozen", result["reasons"][0])

    def test_full_producer_C2_only(self):
        """RED-11: C2-only via insufficient evidence."""
        bundle = dict(self._valid_bundle())
        bundle["memory_records"] = [
            {"id": "m1", "confidence": "confirmed", "status": "active", "superseded_by": []},
            {"id": "m2", "confidence": "inferred", "status": "active", "superseded_by": []},
            {"id": "m3", "confidence": "uncertain", "status": "active", "superseded_by": []},
        ]
        bundle["thresholds"]["verified_ratio_min"] = 0.8
        bundle["thresholds"]["defined_before_run"] = True
        result = self._full_producer_eligibility(**bundle)
        self.assertEqual(result["eligibility_status"], "ineligible")
        self.assertIn("evidence sufficiency is insufficient", result["reasons"][0])

    def test_full_producer_C1_plus_C2(self):
        """RED-11: both C1 and C2 triggers via not-frozen + insufficient."""
        bundle = dict(self._valid_bundle())
        bundle["memory_records"] = [
            {"id": "m1", "confidence": "confirmed", "status": "active", "superseded_by": []},
            {"id": "m2", "confidence": "inferred", "status": "active", "superseded_by": []},
            {"id": "m3", "confidence": "uncertain", "status": "active", "superseded_by": []},
        ]
        bundle["thresholds"]["verified_ratio_min"] = 0.8
        bundle["thresholds"]["defined_before_run"] = False
        result = self._full_producer_eligibility(**bundle)
        self.assertEqual(result["eligibility_status"], "ineligible")
        # Both C1 and C2 should appear
        self.assertIn("thresholds were not frozen", result["reasons"][0])
        self.assertIn("evidence sufficiency is insufficient", result["reasons"][1])


class S54QueueRequestTests(unittest.TestCase):
    """S5.4 Queue Request producer (pure function) — RED/GREEN TDD.

    Plan: S5.4 Implementation Plan r7.1 (baseline v4.3.1, amendment A4 in effect).
    """

    # --- shared fixtures (same idiom as S53EligibilityTests) ---

    def _valid_bundle(self, **overrides):
        bundle = {
            "experiment": {
                "task_id": "task-full-producer",
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
                "task_id": "task-full-producer",
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

    def _full_chain(self, **bundle_overrides):
        """Return (eligibility, reflection, enriched) through the frozen producer chain."""
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation
        from src.learning_loop.reflection_builder import build_reflection
        from src.learning_loop.eligibility import build_eligibility
        bundle = self._valid_bundle(**bundle_overrides)
        enriched = build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))
        reflection = build_reflection(enriched)
        eligibility = build_eligibility(reflection, enriched)
        return eligibility, reflection, enriched

    @staticmethod
    def _oracle_canonical(value):
        """Test-local independent canonicalizer (Appendix C oracle)."""
        import json
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        )

    # --- S5.4 RED-0: scaffold ---

    def test_queue_request_imports(self):
        """RED-0: build_review_queue_request and QueueRequestInputError are importable."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        self.assertTrue(callable(build_review_queue_request))
        self.assertTrue(issubclass(QueueRequestInputError, ValueError))

    def test_input_error_has_gate_and_check(self):
        """RED-0: QueueRequestInputError carries structured gate/check attributes."""
        from src.learning_loop.queue_request import QueueRequestInputError
        err = QueueRequestInputError(gate="shape", message="test")
        self.assertEqual(err.gate, "shape")
        self.assertIsNone(err.check)
        err2 = QueueRequestInputError(gate="upstream", check="anchor-1", message="fail")
        self.assertEqual(err2.gate, "upstream")
        self.assertEqual(err2.check, "anchor-1")

    def test_shape_rejects_non_dict_inputs(self):
        """RED-0: structurally invalid inputs fail gate=shape."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        with self.assertRaises(QueueRequestInputError) as ctx:
            build_review_queue_request({}, {}, {})
        self.assertEqual(ctx.exception.gate, "shape")

    # --- S5.4 RED-0: schema and frozen fields (baseline v4.3.1 §6) ---

    def test_schema_exact_12_keys(self):
        """RED-0: artifact has exactly the 12 frozen top-level keys."""
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        self.assertEqual(
            set(rq.keys()),
            {
                "schema_version", "template_version", "review_queue_item_id",
                "source_eligibility_id", "source_evaluation_id",
                "source_snapshot_digest", "experiment", "status", "summary",
                "evidence_summary", "missing_information", "decision_options",
            },
        )

    def test_status_pending_and_versions(self):
        """RED-0: status is immutably "pending"; both versions are 1."""
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        self.assertEqual(rq["status"], "pending")
        self.assertEqual(rq["schema_version"], 1)
        self.assertEqual(rq["template_version"], 1)

    def test_decision_options_frozen(self):
        """RED-0: decision_options is exactly the frozen 4-item list in order."""
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        self.assertEqual(rq["decision_options"], ["accept", "reject", "defer", "revise"])

    def test_anchor_fields_copy(self):
        """RED-0: anchors copy from eligibility/reflection (§3.1 equalities 1,3,4)."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(rq["source_eligibility_id"], eligibility["eligibility_id"])
        self.assertEqual(rq["source_evaluation_id"], reflection["source_evaluation_id"])
        self.assertEqual(rq["source_snapshot_digest"], reflection["source_snapshot_digest"])

    def test_experiment_copy_only(self):
        """RED-0: experiment block copies the three snapshot fields verbatim."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(
            rq["experiment"],
            enriched["source_evaluation_snapshot"]["experiment"],
        )

    def test_evidence_summary_copy_only(self):
        """RED-0: evidence_summary copies outcome_snapshot fields one-to-one."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        snap = reflection["outcome_snapshot"]
        self.assertEqual(
            rq["evidence_summary"],
            {
                "validation_verdict": snap["validation_verdict"],
                "pack_utility_delta": snap["pack_utility_delta"],
                "verified": snap["evidence_composition"]["verified"],
                "unknown": snap["evidence_composition"]["unknown"],
                "contradicted": snap["evidence_composition"]["contradicted"],
            },
        )

    def test_missing_information_bytewise_copy(self):
        """RED-0: missing_information equals reflection's array, same order."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(
            self._oracle_canonical(rq["missing_information"]),
            self._oracle_canonical(reflection["missing_information"]),
        )

    # --- S5.4 RED-0: identity (baseline v4.3.1 §8) ---

    def test_rq_id_syntax(self):
        """RED-0: review_queue_item_id matches ^rq_[0-9a-f]{64}$."""
        import re
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        self.assertRegex(rq["review_queue_item_id"], r"^rq_[0-9a-f]{64}$")

    def test_rq_id_payload_only_source_eligibility_id(self):
        """RED-0: rq ID = sha256(canonical({source_eligibility_id})), versions excluded."""
        import hashlib
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        payload = self._oracle_canonical(
            {"source_eligibility_id": eligibility["eligibility_id"]}
        )
        expected = "rq_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.assertEqual(rq["review_queue_item_id"], expected)

    def test_determinism_byte_identical(self):
        """RED-0: two builds over the same inputs are canonically byte-identical."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        a = build_review_queue_request(eligibility, reflection, enriched)
        b = build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(self._oracle_canonical(a), self._oracle_canonical(b))

    # --- S5.4 RED-0: SUMMARY literal (Appendix A + C tokens) ---

    def test_summary_frozen_literal(self):
        """RED-0: SUMMARY renders the frozen template with JSON string tokens."""
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        self.assertEqual(
            rq["summary"],
            'experiment "task-full-producer": runs "run-a" -> "run-b", verdict "A".',
        )

    def test_summary_nonascii_task_id_escaped(self):
        """RED-0: non-ASCII task_id renders as \\uXXXX JSON escapes (golden literal)."""
        from src.learning_loop.queue_request import build_review_queue_request
        bundle_exp = {
            "task_id": "任务-1",
            "without_memory_run_id": "run-a",
            "with_memory_run_id": "run-b",
        }
        comparison = {
            "task_id": "任务-1",
            "first_run_id": "run-a",
            "second_run_id": "run-b",
            "first_score": 0.2,
            "second_score": 0.5,
            "score_delta": 0.3,
        }
        rq = build_review_queue_request(
            *self._full_chain(experiment=bundle_exp, comparison=comparison)
        )
        self.assertEqual(
            rq["summary"],
            'experiment "\\u4efb\\u52a1-1": runs "run-a" -> "run-b", verdict "A".',
        )

    def test_summary_embedded_quote_escaped(self):
        """RED-0: run_id with an embedded double quote is JSON-escaped in SUMMARY."""
        from src.learning_loop.queue_request import build_review_queue_request
        quoted = 'run-"b"'
        bundle_exp = {
            "task_id": "task-full-producer",
            "without_memory_run_id": "run-a",
            "with_memory_run_id": quoted,
        }
        overrides = {
            "experiment": bundle_exp,
            "with_memory_outcome": {
                "run_id": quoted, "score": 0.5, "used_memory_ids": ["memory-1"],
            },
            "comparison": {
                "task_id": "task-full-producer",
                "first_run_id": "run-a",
                "second_run_id": quoted,
                "first_score": 0.2,
                "second_score": 0.5,
                "score_delta": 0.3,
            },
        }
        rq = build_review_queue_request(*self._full_chain(**overrides))
        self.assertEqual(
            rq["summary"],
            'experiment "task-full-producer": runs "run-a" -> "run-\\"b\\"", verdict "A".',
        )

    # --- S5.4 RED-0: gate:upstream and ineligible rejection ---

    def test_ineligible_rejected(self):
        """RED-0: ineligible eligibility (C chain) fails gate=ineligible."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        bundle = self._valid_bundle()
        bundle["thresholds"]["defined_before_run"] = False
        eligibility, reflection, enriched = self._full_chain(**bundle)
        self.assertEqual(eligibility["eligibility_status"], "ineligible")
        with self.assertRaises(QueueRequestInputError) as ctx:
            build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(ctx.exception.gate, "ineligible")

    def test_upstream_tampered_status(self):
        """RED-0: hand-flipped eligibility_status fails gate=upstream, not ineligible."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        eligibility, reflection, enriched = self._full_chain()
        eligibility = dict(eligibility)
        eligibility["matched_case"] = "case_09"
        with self.assertRaises(QueueRequestInputError) as ctx:
            build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_forged_eligibility_id(self):
        """RED-0: forged eligibility_id fails gate=upstream."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        eligibility, reflection, enriched = self._full_chain()
        eligibility = dict(eligibility)
        eligibility["eligibility_id"] = "elig_" + "f" * 64
        with self.assertRaises(QueueRequestInputError) as ctx:
            build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(ctx.exception.gate, "upstream")

    def test_upstream_cross_pair_mismatch(self):
        """RED-0: eligibility from a different chain fails gate=upstream."""
        from src.learning_loop.queue_request import (
            build_review_queue_request,
            QueueRequestInputError,
        )
        elig_a, _, _ = self._full_chain()
        _, ref_b, enr_b = self._full_chain(
            thresholds={
                "utility_delta_min": 0.4,
                "verified_ratio_min": 0.5,
                "defined_before_run": True,
            }
        )
        with self.assertRaises(QueueRequestInputError) as ctx:
            build_review_queue_request(elig_a, ref_b, enr_b)
        self.assertEqual(ctx.exception.gate, "upstream")

    # --- S5.4 RED-0: no-aliasing, forbidden words, oracle bytes ---

    def test_alias_free_bidirectional(self):
        """RED-0: deep-mutating inputs after build leaves output unchanged and vice versa."""
        from src.learning_loop.queue_request import build_review_queue_request
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        before = self._oracle_canonical(rq)
        reflection["missing_information"][0]["statement"] = "tampered"
        reflection["outcome_snapshot"]["validation_verdict"] = "B"
        eligibility["reasons"].append("tampered")
        self.assertEqual(self._oracle_canonical(rq), before)
        rq["experiment"]["task_id"] = "tampered"
        rq["missing_information"].append({"x": 1})
        elig2, ref2, enr2 = self._full_chain()
        rq2 = build_review_queue_request(elig2, ref2, enr2)
        self.assertEqual(self._oracle_canonical(rq2), before)

    def test_forbidden_words_absent_from_keys(self):
        """RED-0: closed forbidden-word set absent from all artifact key names."""
        from src.learning_loop.queue_request import build_review_queue_request
        rq = build_review_queue_request(*self._full_chain())
        forbidden = {
            "trust", "ranking", "weight", "per_memory_utility",
            "recommendation", "remove", "delete",
        }
        def scan(node):
            if isinstance(node, dict):
                for key, val in node.items():
                    for word in forbidden:
                        self.assertNotIn(word, key.casefold())
                    scan(val)
            elif isinstance(node, list):
                for item in node:
                    scan(item)
        scan(rq)

    def test_artifact_matches_independent_oracle_bytes(self):
        """RED-0: module's canonical bytes for the artifact equal the test-local oracle."""
        from src.learning_loop.queue_request import build_review_queue_request, _canonical_json
        eligibility, reflection, enriched = self._full_chain()
        rq = build_review_queue_request(eligibility, reflection, enriched)
        self.assertEqual(_canonical_json(rq), self._oracle_canonical(rq))


class _Crash(Exception):
    """Injected crash marker for S5.4 persistence fault-injection tests."""


class S54PersistenceTests(unittest.TestCase):
    """S5.4 persistence layer (state_dir chain E->R->El->Q) — RED/GREEN TDD.

    Plan: S5.4 Implementation Plan r7.1 (baseline v4.3.1, amendment A4 in effect).
    Frozen public surface under test:
      persist_learning_chain(state_dir, enriched, *, forbidden_roots=())
      resume_learning_chain(state_dir, evaluation_id, source_snapshot_digest, *, forbidden_roots=())
      PersistenceError(ValueError) with .gate/.code
      module-level I/O shim `_io` exposing exactly the 12 frozen ops
    """

    maxDiff = None

    _SHIM_OPS = (
        "lstat", "scandir_names", "read_bytes", "open_exclusive", "write_all",
        "close", "fsync_file", "fsync_dir", "mkdir", "link", "unlink",
        "resolve_path",
    )

    # --- fixtures ---

    def _valid_bundle(self, **overrides):
        bundle = {
            "experiment": {
                "task_id": "task-persist",
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
            },
            "without_memory_outcome": {
                "run_id": "run-a", "score": 0.2, "used_memory_ids": [],
            },
            "with_memory_outcome": {
                "run_id": "run-b", "score": 0.5, "used_memory_ids": ["memory-1"],
            },
            "comparison": {
                "task_id": "task-persist",
                "first_run_id": "run-a",
                "second_run_id": "run-b",
                "first_score": 0.2,
                "second_score": 0.5,
                "score_delta": 0.3,
            },
            "memory_records": [
                {
                    "id": "memory-1", "confidence": "confirmed",
                    "status": "active", "superseded_by": [],
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

    def _enriched(self, **bundle_overrides):
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation
        bundle = self._valid_bundle(**bundle_overrides)
        return build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))

    def _ineligible_enriched(self):
        bundle = self._valid_bundle()
        bundle["thresholds"]["defined_before_run"] = False
        return self._enriched(**bundle)

    @staticmethod
    def _oracle_canonical(value):
        import json
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        )

    def _state_dir(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="s54-state-")
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        return tmp

    def _paths(self, state_dir, enriched):
        """Oracle-computed layout paths for a chain."""
        import hashlib
        from pathlib import Path
        snap = enriched["source_evaluation_snapshot"]
        eval_id = enriched["source_evaluation_id"]
        digest = "snap_" + hashlib.sha256(
            self._oracle_canonical(snap).encode("utf-8")
        ).hexdigest()
        digest_dir = Path(state_dir) / "evaluations" / eval_id / digest
        return {
            "eval_id": eval_id,
            "digest": digest,
            "digest_dir": digest_dir,
            "E": digest_dir / "enriched_utility_evaluation.json",
            "R": digest_dir / "reflection.json",
            "El": digest_dir / "eligibility.json",
            "queue_dir": Path(state_dir) / "review_queue",
        }

    def _rq_path(self, state_dir, eligibility_id):
        import hashlib
        from pathlib import Path
        payload = self._oracle_canonical({"source_eligibility_id": eligibility_id})
        rq_id = "rq_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return Path(state_dir) / "review_queue" / (rq_id + ".json")

    def _persist(self, state_dir, enriched, **kw):
        from src.learning_loop.persistence import persist_learning_chain
        return persist_learning_chain(state_dir, enriched, **kw)

    def _resume(self, state_dir, eval_id, digest, **kw):
        from src.learning_loop.persistence import resume_learning_chain
        return resume_learning_chain(state_dir, eval_id, digest, **kw)

    def _tree_bytes(self, state_dir):
        """Map of relative path -> bytes for every regular file under state_dir."""
        from pathlib import Path
        out = {}
        for p in sorted(Path(state_dir).rglob("*")):
            if p.is_file() and not p.is_symlink():
                out[str(p.relative_to(state_dir))] = p.read_bytes()
        return out

    def _record_ops(self):
        """Patch every shim op to record (name, first-arg) while calling through."""
        from unittest import mock
        from src.learning_loop import persistence
        calls = []
        def wrap(name, real):
            def inner(*args, **kwargs):
                calls.append((name, args[0] if args else None))
                return real(*args, **kwargs)
            return inner
        for name in self._SHIM_OPS:
            real = getattr(persistence._io, name)
            patcher = mock.patch.object(persistence._io, name, wrap(name, real))
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def _err(self):
        from src.learning_loop.persistence import PersistenceError
        return PersistenceError

    # --- S5.4 RED-2: fs preconditions and path gate ---

    def test_persistence_imports(self):
        """RED-2: entries, error type, and 12-op shim are importable/frozen."""
        from src.learning_loop.persistence import (
            persist_learning_chain, resume_learning_chain, PersistenceError, _io,
        )
        self.assertTrue(callable(persist_learning_chain))
        self.assertTrue(callable(resume_learning_chain))
        self.assertTrue(issubclass(PersistenceError, ValueError))
        for op in self._SHIM_OPS:
            self.assertTrue(callable(getattr(_io, op)), op)

    def test_persistence_error_gate_code(self):
        """RED-2: PersistenceError carries gate and code."""
        err = self._err()(gate="path", code="missing_state_dir", message="x")
        self.assertEqual(err.gate, "path")
        self.assertEqual(err.code, "missing_state_dir")

    def test_state_dir_must_exist_and_not_be_created(self):
        """RED-2 (r6-M1): missing state_dir -> gate=path, root not created."""
        import os
        state = self._state_dir()
        missing = os.path.join(state, "does-not-exist")
        with self.assertRaises(self._err()) as ctx:
            self._persist(missing, self._enriched())
        self.assertEqual(ctx.exception.gate, "path")
        self.assertFalse(os.path.exists(missing))

    def test_state_dir_file_rejected(self):
        """RED-2 (r6-M1): state_dir that is a regular file -> gate=path."""
        import os
        state = self._state_dir()
        f = os.path.join(state, "afile")
        open(f, "w").close()
        with self.assertRaises(self._err()) as ctx:
            self._persist(f, self._enriched())
        self.assertEqual(ctx.exception.gate, "path")

    def test_state_dir_symlink_rejected(self):
        """RED-2 (r3ext-D3): symlinked state_dir -> gate=path."""
        import os
        state = self._state_dir()
        real = os.path.join(state, "real")
        os.mkdir(real)
        link = os.path.join(state, "link")
        os.symlink(real, link)
        with self.assertRaises(self._err()) as ctx:
            self._persist(link, self._enriched())
        self.assertEqual(ctx.exception.gate, "path")

    def test_forbidden_roots_rejected(self):
        """RED-2 (r4-C2): state_dir under caller-provided forbidden root -> gate=path."""
        import os
        parent = self._state_dir()
        state = os.path.join(parent, "state")
        os.mkdir(state)
        with self.assertRaises(self._err()) as ctx:
            self._persist(state, self._enriched(), forbidden_roots=(parent,))
        self.assertEqual(ctx.exception.gate, "path")

    def test_forbidden_root_symlink_escape_detected(self):
        """RED-2 (r3ext-D3): textual path outside, resolving inside forbidden root -> gate=path."""
        import os
        parent = self._state_dir()
        inner = os.path.join(parent, "inner")
        os.mkdir(inner)
        outside = self._state_dir()
        alias = os.path.join(outside, "alias")
        os.symlink(inner, alias)
        with self.assertRaises(self._err()) as ctx:
            self._persist(alias, self._enriched(), forbidden_roots=(parent,))
        self.assertEqual(ctx.exception.gate, "path")

    def test_repository_root_rejected(self):
        """RED-2 (r4-C2/r7-M1): state_dir inside the repository -> gate=path (lexical self-detect)."""
        import os, tempfile
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(
            __import__("src.learning_loop", fromlist=["__file__"]).__file__
        )))
        state = tempfile.mkdtemp(prefix="s54-in-repo-", dir=repo_root)
        self.addCleanup(__import__("shutil").rmtree, state, True)
        with self.assertRaises(self._err()) as ctx:
            self._persist(state, self._enriched())
        self.assertEqual(ctx.exception.gate, "path")

    def test_child_symlink_directory_rejected(self):
        """RED-2 (r4-C1): symlinked evaluations/ component -> gate=path, no traversal."""
        import os
        state = self._state_dir()
        elsewhere = self._state_dir()
        os.symlink(elsewhere, os.path.join(state, "evaluations"))
        with self.assertRaises(self._err()) as ctx:
            self._persist(state, self._enriched())
        self.assertEqual(ctx.exception.gate, "path")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_probe_leaves_no_residue_on_success(self):
        """RED-2 (r7-m1): successful persist leaves no .tmp.* anywhere (probe dual-unlink)."""
        from pathlib import Path
        state = self._state_dir()
        self._persist(state, self._enriched())
        residues = [p for p in Path(state).rglob(".tmp.*")]
        self.assertEqual(residues, [])

    def test_probe_never_runs_on_corrupt_chain(self):
        """RED-2 (r3ext-D2): corrupt state -> zero mutating ops (probe suppressed)."""
        import os
        state = self._state_dir()
        paths = self._paths(state, self._enriched())
        os.makedirs(paths["digest_dir"])
        paths["R"].write_bytes(b"{}")  # downstream without upstream E = non-prefix
        calls = self._record_ops()
        with self.assertRaises(self._err()):
            self._persist(state, self._enriched())
        mutating = [c for c in calls if c[0] in
                    ("open_exclusive", "write_all", "mkdir", "link")]
        self.assertEqual(mutating, [])
        unlinks = [c for c in calls if c[0] == "unlink"]
        for _, target in unlinks:
            self.assertIn(".tmp.", str(target))

    # --- S5.4 RED-3: typed preflight ---

    def test_persist_eligible_full_chain_layout(self):
        """RED-3: eligible persist writes E/R/El in digest dir and exactly one rq."""
        state = self._state_dir()
        enriched = self._enriched()
        result = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        for key in ("E", "R", "El"):
            self.assertTrue(paths[key].is_file(), key)
        rq = self._rq_path(state, result["eligibility"]["eligibility_id"])
        self.assertTrue(rq.is_file())
        queue_files = [p for p in paths["queue_dir"].iterdir() if p.suffix == ".json"]
        self.assertEqual(queue_files, [rq])

    def test_persist_ineligible_terminates_at_el(self):
        """RED-3 (r1-1): ineligible persist ends at [E,R,El]; no queue artifact."""
        state = self._state_dir()
        enriched = self._ineligible_enriched()
        result = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        for key in ("E", "R", "El"):
            self.assertTrue(paths[key].is_file(), key)
        self.assertIsNone(result["review_queue_request"])
        if paths["queue_dir"].exists():
            self.assertEqual(
                [p for p in paths["queue_dir"].iterdir() if p.suffix == ".json"], []
            )

    def test_persist_idempotent_byte_identical(self):
        """RED-3: second persist over identical input is a byte-identical no-op."""
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        before = self._tree_bytes(state)
        result2 = self._persist(state, enriched)
        self.assertEqual(self._tree_bytes(state), before)
        self.assertIn("eligibility", result2)

    def test_resume_converges_from_every_eligible_prefix(self):
        """RED-3/RED-6: deleting any legal suffix then resume restores identical bytes."""
        state = self._state_dir()
        enriched = self._enriched()
        result = self._persist(state, enriched)
        reference = self._tree_bytes(state)
        paths = self._paths(state, enriched)
        rq = self._rq_path(state, result["eligibility"]["eligibility_id"])
        suffixes = (
            [rq],                        # [E,R,El] crash prefix
            [rq, paths["El"]],           # [E,R]
            [rq, paths["El"], paths["R"]],  # [E]
        )
        for to_delete in suffixes:
            with self.subTest(prefix_missing=[p.name for p in to_delete]):
                for p in to_delete:
                    p.unlink()
                self._resume(state, paths["eval_id"], paths["digest"])
                self.assertEqual(self._tree_bytes(state), reference)

    def test_resume_missing_e_fails_closed(self):
        """RED-3 (r2-B1): E absent with downstream present = non-prefix -> gate=preflight."""
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        paths["E"].unlink()
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_resume_on_empty_state_reports_missing_e(self):
        """RED-3 (r2-B1): resume on legal ∅ fails closed (caller must persist)."""
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_resume_rejects_bad_id_syntax(self):
        """RED-1: malformed evaluation_id / digest -> gate=path before any traversal."""
        state = self._state_dir()
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, "eval_not-hex", "snap_" + "a" * 64)
        self.assertEqual(ctx.exception.gate, "path")
        with self.assertRaises(self._err()) as ctx2:
            self._resume(state, "eval_" + "a" * 64, "SNAP_" + "A" * 64)
        self.assertEqual(ctx2.exception.gate, "path")

    def test_ineligible_with_q_is_corruption(self):
        """RED-3 (r1-1): planted Q on an ineligible chain -> gate=preflight corruption."""
        state = self._state_dir()
        enriched = self._ineligible_enriched()
        result = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        rq = self._rq_path(state, result["eligibility"]["eligibility_id"])
        rq.parent.mkdir(exist_ok=True)
        rq.write_bytes(b"{}")
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_tampered_artifact_detected_and_untouched(self):
        """RED-3 (r1-10): byte-mismatched R -> error; existing file bytes unchanged."""
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        tampered = paths["R"].read_bytes() + b" "
        paths["R"].write_bytes(tampered)
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")
        self.assertEqual(paths["R"].read_bytes(), tampered)

    def test_noncanonical_e_rejected(self):
        """RED-3 (r2-B5): semantically equal but non-canonical E bytes -> gate=preflight."""
        import json
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        loose = json.dumps(json.loads(paths["E"].read_bytes()), indent=2).encode()
        paths["E"].write_bytes(loose)
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_path_binding_implanted_e_rejected(self):
        """RED-1 (r2-B2): valid E implanted under a different eval id dir -> error."""
        import shutil
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        other_eval = "eval_" + "9" * 64
        other_dir = paths["digest_dir"].parent.parent / other_eval / paths["digest"]
        other_dir.mkdir(parents=True)
        shutil.copy2(paths["E"], other_dir / "enriched_utility_evaluation.json")
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, other_eval, paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_empty_directory_skeleton_equals_missing(self):
        """RED-3/RED-11 (r6-m1): pre-created empty digest dir behaves as ∅."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        os.makedirs(paths["digest_dir"])
        self._persist(state, enriched)
        self.assertTrue(paths["E"].is_file())

    def test_unexpected_entry_in_digest_dir_is_corruption(self):
        """RED-3 (r6-m1): non-allowed entry inside digest dir -> gate=preflight."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        os.makedirs(paths["digest_dir"])
        (paths["digest_dir"] / "stray.txt").write_bytes(b"x")
        with self.assertRaises(self._err()) as ctx:
            self._persist(state, enriched)
        self.assertEqual(ctx.exception.gate, "preflight")

    def test_zero_write_preflight_audit(self):
        """RED-3 (r1-11): rejected preflight performs no mutation except .tmp.* unlink."""
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        paths["E"].unlink()  # non-prefix now
        calls = self._record_ops()
        with self.assertRaises(self._err()):
            self._resume(state, paths["eval_id"], paths["digest"])
        for name, target in calls:
            if name in ("open_exclusive", "write_all", "mkdir", "link"):
                self.fail(f"mutating op {name} during failed preflight")
            if name == "unlink":
                self.assertIn(".tmp.", str(target))

    # --- S5.4 RED-4: temp lifecycle ---

    def test_stale_temps_cleaned_everywhere(self):
        """RED-4: stale .tmp.* in digest dir and review_queue are removed."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        os.makedirs(paths["digest_dir"])
        os.makedirs(paths["queue_dir"])
        stale1 = paths["digest_dir"] / ".tmp.reflection.json.deadbeef"
        stale2 = paths["queue_dir"] / ".tmp.rq_x.json.deadbeef"
        stale1.write_bytes(b"junk")
        stale2.write_bytes(b"junk")
        self._persist(state, enriched)
        self.assertFalse(stale1.exists())
        self.assertFalse(stale2.exists())

    def test_symlink_temp_leaf_unlink_no_deref(self):
        """RED-4 (r5-4): symlinked .tmp.* leaf is unlinked; its target survives."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        os.makedirs(paths["queue_dir"])
        outside = self._state_dir()
        target = os.path.join(outside, "victim.json")
        with open(target, "w") as fh:
            fh.write("precious")
        sym = paths["queue_dir"] / ".tmp.victim.json.cafe"
        os.symlink(target, sym)
        self._persist(state, enriched)
        self.assertFalse(sym.exists())
        self.assertTrue(os.path.exists(target))
        with open(target) as fh:
            self.assertEqual(fh.read(), "precious")

    def test_queue_temp_name_content_does_not_change_behaviour(self):
        """RED-4 (r3ext-B1): cleanup blind to temp name content (real rq basename vs junk)."""
        import os
        outcomes = []
        for name in (".tmp.zzzz-not-an-id.json.1234", None):
            state = self._state_dir()
            enriched = self._enriched()
            paths = self._paths(state, enriched)
            os.makedirs(paths["queue_dir"])
            result_probe = self._persist(state, enriched)
            real_rq = self._rq_path(state, result_probe["eligibility"]["eligibility_id"])
            import shutil
            shutil.rmtree(state)
            os.mkdir(state)
            os.makedirs(paths["queue_dir"])
            temp_name = name or (".tmp." + real_rq.name + ".5678")
            (paths["queue_dir"] / temp_name).write_bytes(b"junk")
            result = self._persist(state, enriched)
            outcomes.append(self._oracle_canonical(result))
            self.assertEqual(
                [p.name for p in paths["queue_dir"].iterdir() if p.suffix == ".json"],
                [real_rq.name],
            )
        self.assertEqual(outcomes[0], outcomes[1])

    # --- S5.4 RED-5: publish sequence (shim-op level) ---

    def _record_full(self):
        """Patch every shim op to record (name, args) while calling through."""
        from unittest import mock
        from src.learning_loop import persistence
        calls = []
        def wrap(name, real):
            def inner(*args, **kwargs):
                calls.append((name, args))
                return real(*args, **kwargs)
            return inner
        for name in self._SHIM_OPS:
            real = getattr(persistence._io, name)
            patcher = mock.patch.object(persistence._io, name, wrap(name, real))
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def test_publish_sequence_per_artifact(self):
        """RED-5 (r6-m2): each artifact follows open_exclusive→write_all→fsync_file→close→readback→link→unlink→fsync_dir."""
        state = self._state_dir()
        calls = self._record_full()
        self._persist(state, self._enriched())
        pattern = ["open_exclusive", "write_all", "fsync_file", "close",
                   "read_bytes", "link", "unlink", "fsync_dir"]
        opens = [i for i, (n, _) in enumerate(calls)
                 if n == "open_exclusive" and ".tmp.probe" not in str(calls[i][1][0])]
        self.assertEqual(len(opens), 4)  # E, R, El, Q
        for i in opens:
            names = [n for n, _ in calls[i:i + len(pattern)]]
            self.assertEqual(names, pattern, f"artifact publish at op {i}")
            temp_path = str(calls[i][1][0])
            link_args = calls[i + 5][1]
            self.assertEqual(str(link_args[0]), temp_path)
            base = temp_path.rsplit("/", 1)[-1]
            target_name = str(link_args[1]).rsplit("/", 1)[-1]
            self.assertTrue(base.startswith(".tmp." + target_name + "."),
                            f"temp {base} vs target {target_name}")

    def test_mkdir_followed_by_parent_fsync_root_to_leaf(self):
        """RED-5: every mkdir is followed by fsync_dir of its parent."""
        state = self._state_dir()
        calls = self._record_full()
        self._persist(state, self._enriched())
        import os
        for i, (name, args) in enumerate(calls):
            if name != "mkdir":
                continue
            made = os.path.abspath(str(args[0]))
            parent = os.path.dirname(made)
            later = [os.path.abspath(str(a[0])) for n, a in calls[i + 1:]
                     if n == "fsync_dir"]
            self.assertIn(parent, later, f"mkdir {made} lacks parent fsync")

    def test_short_write_never_reaches_link(self):
        """RED-5 (r6-m2): partial os.write is looped to completion; bytes still exact."""
        import os
        from unittest import mock
        state = self._state_dir()
        enriched = self._enriched()
        real_write = os.write
        def partial_write(fd, data):
            return real_write(fd, bytes(data)[:5]) if len(data) > 5 else real_write(fd, data)
        with mock.patch("os.write", side_effect=partial_write):
            self._persist(state, enriched)
        paths = self._paths(state, enriched)
        import json
        parsed = json.loads(paths["E"].read_bytes())
        self.assertEqual(parsed["source_evaluation_id"], enriched["source_evaluation_id"])
        self.assertEqual(
            paths["E"].read_bytes().decode(),
            self._oracle_canonical(parsed),
        )

    def test_no_os_replace_in_source(self):
        """RED-5 (r1-10): os.replace must not appear in persistence source."""
        import inspect
        from src.learning_loop import persistence
        self.assertNotIn("os.replace", inspect.getsource(persistence))

    def test_conflicting_target_untouched_on_error(self):
        """RED-5/RED-3 (r2-B3-10): link-exists with different bytes -> error, target intact."""
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        forged = b'{"forged":true}'
        paths["El"].write_bytes(forged)
        with self.assertRaises(self._err()):
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(paths["El"].read_bytes(), forged)

    # --- S5.4 RED-10: chain-level Q-last ordering ---

    def test_queue_publication_strictly_after_el(self):
        """RED-10 (r1-8): no review_queue publication op before El's final fsync_dir."""
        state = self._state_dir()
        calls = self._record_full()
        self._persist(state, self._enriched())
        import os
        digest_dir = None
        queue_first = None
        el_fsync = None
        for i, (name, args) in enumerate(calls):
            arg0 = str(args[0]) if args else ""
            if "review_queue" in arg0:
                if name == "unlink" and ".tmp." in arg0:
                    continue  # A4 cleanup exemption
                if name in ("mkdir", "open_exclusive", "write_all", "link",
                            "fsync_dir") and queue_first is None:
                    queue_first = i
            if name == "fsync_dir" and "evaluations" in arg0 and "snap_" in arg0:
                el_fsync = i
        self.assertIsNotNone(queue_first)
        self.assertIsNotNone(el_fsync)
        self.assertGreater(queue_first, el_fsync)

    def test_queue_cleanup_at_empty_state_passes_with_qlast(self):
        """RED-10 (r2-C1): stale queue temp at ∅ is cleaned early without violating Q-last."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        paths = self._paths(state, enriched)
        os.makedirs(paths["queue_dir"])
        stale = paths["queue_dir"] / ".tmp.stale.json.beef"
        stale.write_bytes(b"junk")
        result = self._persist(state, enriched)
        self.assertFalse(stale.exists())
        rq = self._rq_path(state, result["eligibility"]["eligibility_id"])
        self.assertTrue(rq.is_file())

    def test_no_visible_rq_before_q_link(self):
        """RED-10: crash immediately before Q link leaves review_queue without targets."""
        from unittest import mock
        from src.learning_loop import persistence
        state = self._state_dir()
        enriched = self._enriched()
        real_link = persistence._io.link
        def crashing_link(src, dst, *a, **kw):
            if "review_queue" in str(dst):
                raise _Crash()
            return real_link(src, dst, *a, **kw)
        with mock.patch.object(persistence._io, "link", crashing_link):
            with self.assertRaises((_Crash, self._err())):
                self._persist(state, enriched)
        paths = self._paths(state, enriched)
        if paths["queue_dir"].exists():
            self.assertEqual(
                [p for p in paths["queue_dir"].iterdir() if p.suffix == ".json"], []
            )

    # --- S5.4 RED-6: crash-replay convergence matrix ---

    def _run_with_crash(self, state, enriched, crash_at, after=False):
        """Run persist with a crash injected at shim-op index crash_at."""
        from unittest import mock
        from src.learning_loop import persistence
        counter = {"n": 0}
        def wrap(real):
            def inner(*args, **kwargs):
                idx = counter["n"]
                counter["n"] += 1
                if idx == crash_at and not after:
                    raise _Crash()
                result = real(*args, **kwargs)
                if idx == crash_at and after:
                    raise _Crash()
                return result
            return inner
        patchers = []
        for name in self._SHIM_OPS:
            p = mock.patch.object(persistence._io, name,
                                  wrap(getattr(persistence._io, name)))
            p.start()
            patchers.append(p)
        try:
            with self.assertRaises(_Crash):
                self._persist(state, enriched)
        finally:
            for p in patchers:
                p.stop()

    def _recover(self, state, enriched):
        """State-observed routing: resume if E on disk, else persist (⚙-4)."""
        paths = self._paths(state, enriched)
        if paths["E"].is_file():
            return self._resume(state, paths["eval_id"], paths["digest"])
        return self._persist(state, enriched)

    def _count_clean_ops(self, enriched):
        state = self._state_dir()
        calls = self._record_full()
        self._persist(state, enriched)
        return len(calls), self._tree_bytes(state)

    def test_crash_sweep_depth1_converges(self):
        """RED-6: crash before AND after every shim op -> recovery converges bytewise."""
        enriched = self._enriched()
        total, reference = self._count_clean_ops(enriched)
        self.assertGreater(total, 10)
        for after in (False, True):
            for k in range(total):
                with self.subTest(crash_at=k, after=after):
                    state = self._state_dir()
                    try:
                        self._run_with_crash(state, enriched, k, after=after)
                    except AssertionError:
                        continue  # ops after publication finished; nothing to crash
                    self._recover(state, enriched)
                    self.assertEqual(self._tree_bytes(state), reference)

    def test_crash_sweep_ineligible_chain(self):
        """RED-6: crash sweep over the ineligible chain converges to [E,R,El]."""
        enriched = self._ineligible_enriched()
        total, reference = self._count_clean_ops(enriched)
        for k in range(total):
            with self.subTest(crash_at=k):
                state = self._state_dir()
                try:
                    self._run_with_crash(state, enriched, k)
                except AssertionError:
                    continue
                self._recover(state, enriched)
                self.assertEqual(self._tree_bytes(state), reference)

    def test_crash_depth2_recovery_convergence(self):
        """RED-6 (r2-B6): crash during recovery, recover again (depth 2) -> converges."""
        enriched = self._enriched()
        total, reference = self._count_clean_ops(enriched)
        from unittest import mock
        from src.learning_loop import persistence
        probe_points = list(range(0, total, 7)) or [0]
        for k in probe_points:
            state = self._state_dir()
            try:
                self._run_with_crash(state, enriched, k)
            except AssertionError:
                continue
            for j in (0, 3, 9):
                with self.subTest(first=k, second=j):
                    counter = {"n": 0}
                    def wrap(real):
                        def inner(*args, **kwargs):
                            idx = counter["n"]
                            counter["n"] += 1
                            if idx == j:
                                raise _Crash()
                            return real(*args, **kwargs)
                        return inner
                    patchers = [mock.patch.object(
                        persistence._io, name,
                        wrap(getattr(persistence._io, name)))
                        for name in self._SHIM_OPS]
                    for p in patchers:
                        p.start()
                    try:
                        self._recover(state, enriched)
                    except _Crash:
                        pass
                    finally:
                        for p in patchers:
                            p.stop()
            self._recover(state, enriched)
            self.assertEqual(self._tree_bytes(state), reference)

    def test_link_window_dual_outcomes_per_artifact(self):
        """RED-6 (r3ext-A1): ③–⑤ window for each artifact, entry survived vs lost."""
        enriched = self._enriched()
        _, reference = self._count_clean_ops(enriched)
        basenames = ("enriched_utility_evaluation.json", "reflection.json",
                     "eligibility.json", "queue")
        from unittest import mock
        from src.learning_loop import persistence
        for base in basenames:
            for lost in (False, True):
                with self.subTest(artifact=base, entry_lost=lost):
                    state = self._state_dir()
                    linked = {}
                    real_link = persistence._io.link
                    def crash_after_link(src, dst, *a, **kw):
                        result = real_link(src, dst, *a, **kw)
                        name = str(dst)
                        match = (base in name) if base != "queue" else ("review_queue" in name)
                        if match and ".tmp.probe" not in name:
                            linked["dst"] = name
                            raise _Crash()
                        return result
                    with mock.patch.object(persistence._io, "link", crash_after_link):
                        with self.assertRaises(_Crash):
                            self._persist(state, enriched)
                    if lost:
                        import os
                        if os.path.exists(linked["dst"]):
                            os.unlink(linked["dst"])
                    self._recover(state, enriched)
                    self.assertEqual(self._tree_bytes(state), reference)

    def test_recovery_refsyncs_surviving_dirs_and_targets(self):
        """RED-6 (r4-B1/r5-1): idempotent resume re-fsyncs every publication dir level."""
        import os
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        calls = self._record_full()
        self._resume(state, paths["eval_id"], paths["digest"])
        fsynced = {os.path.abspath(str(a[0])) for n, a in calls if n == "fsync_dir"}
        expected = {
            os.path.abspath(state),
            os.path.abspath(os.path.join(state, "evaluations")),
            os.path.abspath(str(paths["digest_dir"].parent)),
            os.path.abspath(str(paths["digest_dir"])),
            os.path.abspath(str(paths["queue_dir"])),
        }
        self.assertTrue(expected.issubset(fsynced),
                        f"missing re-fsync: {expected - fsynced}")
        el_fsyncs = [i for i, (n, a) in enumerate(calls) if n == "fsync_dir"
                     and os.path.abspath(str(a[0])) == os.path.abspath(str(paths["digest_dir"]))]
        queue_pubs = [i for i, (n, a) in enumerate(calls)
                      if "review_queue" in str(a[0] if a else "")
                      and n in ("open_exclusive", "link", "mkdir")]
        if queue_pubs:
            self.assertLess(min(el_fsyncs), min(queue_pubs))

    # --- S5.4 RED-7: E5 closure (three components) ---

    def test_e5_digest_directory_equality(self):
        """RED-7 (E5-1): recomputed digest == dir segment == R.source_snapshot_digest."""
        import hashlib, json
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        disk_e = json.loads(paths["E"].read_bytes())
        recomputed = "snap_" + hashlib.sha256(
            self._oracle_canonical(disk_e["source_evaluation_snapshot"]).encode()
        ).hexdigest()
        self.assertEqual(recomputed, paths["digest_dir"].name)
        disk_r = json.loads(paths["R"].read_bytes())
        self.assertEqual(recomputed, disk_r["source_snapshot_digest"])

    def test_e5_derived_artifacts_rebuild_byte_identical(self):
        """RED-7 (E5-2): R/El/Q rebuilt from disk E match disk bytes exactly."""
        import json
        from src.learning_loop.reflection_builder import build_reflection
        from src.learning_loop.eligibility import build_eligibility
        from src.learning_loop.queue_request import build_review_queue_request
        state = self._state_dir()
        enriched = self._enriched()
        result = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        disk_e = json.loads(paths["E"].read_bytes())
        ref = build_reflection(disk_e)
        elig = build_eligibility(ref, disk_e)
        rq = build_review_queue_request(elig, ref, disk_e)
        self.assertEqual(paths["R"].read_bytes().decode(), self._oracle_canonical(ref))
        self.assertEqual(paths["El"].read_bytes().decode(), self._oracle_canonical(elig))
        rq_path = self._rq_path(state, result["eligibility"]["eligibility_id"])
        self.assertEqual(rq_path.read_bytes().decode(), self._oracle_canonical(rq))

    def test_e5_e_canonical_self_check(self):
        """RED-7 (E5-3): disk E bytes == canonical(parse(bytes))."""
        import json
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        raw = paths["E"].read_bytes()
        self.assertEqual(raw.decode(), self._oracle_canonical(json.loads(raw)))

    def test_snapshot_instance_difference_coexists(self):
        """RED-7 (§12-5): same evaluation_id, different snapshot -> disjoint digest dirs."""
        import copy
        state = self._state_dir()
        enriched_a = self._enriched()
        enriched_b = copy.deepcopy(enriched_a)
        enriched_b["source_evaluation_snapshot"]["staleness_warning"] = True
        self._persist(state, enriched_a)
        self._persist(state, enriched_b)
        pa = self._paths(state, enriched_a)
        pb = self._paths(state, enriched_b)
        self.assertEqual(pa["eval_id"], pb["eval_id"])
        self.assertNotEqual(pa["digest"], pb["digest"])
        self.assertTrue(pa["E"].is_file())
        self.assertTrue(pb["E"].is_file())
        self.assertNotEqual(pa["R"].read_bytes(), pb["R"].read_bytes())

    # --- S5.4 RED-8: version fail-closed ---

    def test_rq_version_drift_no_second_pending(self):
        """RED-8: on-disk rq with different schema_version -> error, exactly one pending."""
        import json
        state = self._state_dir()
        enriched = self._enriched()
        result = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        rq_path = self._rq_path(state, result["eligibility"]["eligibility_id"])
        drifted = json.loads(rq_path.read_bytes())
        drifted["schema_version"] = 2
        rq_path.write_bytes(self._oracle_canonical(drifted).encode())
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")
        pendings = [p for p in paths["queue_dir"].iterdir() if p.suffix == ".json"]
        self.assertEqual(len(pendings), 1)

    def test_rf_version_drift_detected(self):
        """RED-8: on-disk reflection with drifted template_version -> byte compare error."""
        import json
        state = self._state_dir()
        enriched = self._enriched()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        drifted = json.loads(paths["R"].read_bytes())
        drifted["template_version"] = 2
        paths["R"].write_bytes(self._oracle_canonical(drifted).encode())
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")

    # --- S5.4 RED-9: I/O boundary audits ---

    def test_queue_request_module_no_io(self):
        """RED-9: build_review_queue_request performs zero fs I/O (audit-hook subprocess)."""
        import subprocess, sys, os
        script = r"""
import sys
sys.path.insert(0, ".")
from src.learning_loop.evaluation import evaluate_experiment_bundle
from src.learning_loop.enrichment import build_enriched_utility_evaluation
from src.learning_loop.reflection_builder import build_reflection
from src.learning_loop.eligibility import build_eligibility
from src.learning_loop.queue_request import build_review_queue_request

bundle = {
    "experiment": {"task_id": "t", "without_memory_run_id": "a", "with_memory_run_id": "b"},
    "without_memory_outcome": {"run_id": "a", "score": 0.2, "used_memory_ids": []},
    "with_memory_outcome": {"run_id": "b", "score": 0.5, "used_memory_ids": ["m1"]},
    "comparison": {"task_id": "t", "first_run_id": "a", "second_run_id": "b",
                   "first_score": 0.2, "second_score": 0.5, "score_delta": 0.3},
    "memory_records": [{"id": "m1", "confidence": "confirmed", "status": "active", "superseded_by": []}],
    "thresholds": {"utility_delta_min": 0.1, "verified_ratio_min": 0.5, "defined_before_run": True},
}
enr = build_enriched_utility_evaluation(evaluate_experiment_bundle(bundle))
ref = build_reflection(enr)
elig = build_eligibility(ref, enr)

events = []
def hook(event, args):
    if event == "open" or event.startswith("os.") or event.startswith("shutil."):
        events.append(event)
sys.addaudithook(hook)

rq = build_review_queue_request(elig, ref, enr)
assert rq["status"] == "pending"
if events:
    print("FAIL: I/O events:", events)
    sys.exit(1)
print("OK")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_persistence_mutations_confined_to_state_dir(self):
        """RED-9 (r7-M1): every mutating shim op targets the state_dir subtree."""
        import os
        state = self._state_dir()
        calls = self._record_full()
        self._persist(state, self._enriched())
        root = os.path.realpath(state)
        for name, args in calls:
            if name in ("open_exclusive", "write_all", "fsync_file", "close"):
                continue  # fd-based or covered via open_exclusive path below
            if name in ("mkdir", "link", "unlink", "fsync_dir"):
                for a in args:
                    p = os.path.realpath(str(a))
                    self.assertTrue(
                        p == root or p.startswith(root + os.sep),
                        f"{name} escaped state_dir: {p}",
                    )
        opens = [args[0] for name, args in calls if name == "open_exclusive"]
        for a in opens:
            p = os.path.realpath(os.path.dirname(str(a)))
            self.assertTrue(p == root or p.startswith(root + os.sep))

    def test_disk_queries_only_via_shim(self):
        """RED-9 (r6-M2): no Path.exists/is_file/resolve/os.path.exists outside the _Io shim."""
        import inspect, re
        from src.learning_loop import persistence
        source = inspect.getsource(persistence)
        shim_match = re.search(r"class _Io\b.*?(?=\nclass |\Z)", source, re.S)
        self.assertIsNotNone(shim_match, "shim must be a class named _Io")
        outside = source.replace(shim_match.group(0), "")
        for token in (".exists(", ".is_file(", ".is_dir(", ".resolve(",
                      "os.path.exists", "os.path.isfile", "os.path.isdir",
                      "os.stat(", "os.lstat(", "os.scandir", "os.listdir"):
            self.assertNotIn(token, outside, f"disk query outside shim: {token}")

    # --- S5.4 RED-11: API contract ---

    def test_return_structure_identical_across_outcomes(self):
        """RED-11 (r6-M3): first write, idempotent replay, resume return the same structure."""
        state = self._state_dir()
        enriched = self._enriched()
        first = self._persist(state, enriched)
        replay = self._persist(state, enriched)
        paths = self._paths(state, enriched)
        resumed = self._resume(state, paths["eval_id"], paths["digest"])
        for result in (first, replay, resumed):
            self.assertEqual(set(result.keys()), {"eligibility", "review_queue_request"})
        self.assertEqual(self._oracle_canonical(first), self._oracle_canonical(replay))
        self.assertEqual(self._oracle_canonical(first), self._oracle_canonical(resumed))
        ineligible = self._ineligible_enriched()
        state2 = self._state_dir()
        res_inelig = self._persist(state2, ineligible)
        self.assertEqual(set(res_inelig.keys()), {"eligibility", "review_queue_request"})
        self.assertIsNone(res_inelig["review_queue_request"])

    def test_return_value_alias_free(self):
        """RED-11 (r6-M3): mutating a returned structure never affects later results."""
        state = self._state_dir()
        enriched = self._enriched()
        first = self._persist(state, enriched)
        canon = self._oracle_canonical(first)
        first["eligibility"]["reasons"].append("tampered")
        first["review_queue_request"]["status"] = "tampered"
        second = self._persist(state, enriched)
        self.assertEqual(self._oracle_canonical(second), canon)

    def test_five_gates_each_triggerable(self):
        """RED-11 (r6-M3): path/preflight/capability/publish/readback gates all reachable."""
        import os
        from unittest import mock
        from src.learning_loop import persistence
        enriched = self._enriched()
        # path
        state = self._state_dir()
        with self.assertRaises(self._err()) as ctx:
            self._persist(os.path.join(state, "nope"), enriched)
        self.assertEqual(ctx.exception.gate, "path")
        # preflight
        state = self._state_dir()
        self._persist(state, enriched)
        paths = self._paths(state, enriched)
        paths["E"].unlink()
        with self.assertRaises(self._err()) as ctx:
            self._resume(state, paths["eval_id"], paths["digest"])
        self.assertEqual(ctx.exception.gate, "preflight")
        # capability
        state = self._state_dir()
        real_link = persistence._io.link
        def probe_fail(src, dst, *a, **kw):
            if ".tmp.probe" in str(dst) or ".tmp.probe" in str(src):
                raise OSError("no link support")
            return real_link(src, dst, *a, **kw)
        with mock.patch.object(persistence._io, "link", probe_fail):
            with self.assertRaises(self._err()) as ctx:
                self._persist(state, enriched)
        self.assertEqual(ctx.exception.gate, "capability")
        # publish
        state = self._state_dir()
        def artifact_fail(src, dst, *a, **kw):
            if "evaluations" in str(dst):
                raise OSError("boom")
            return real_link(src, dst, *a, **kw)
        with mock.patch.object(persistence._io, "link", artifact_fail):
            with self.assertRaises(self._err()) as ctx:
                self._persist(state, enriched)
        self.assertEqual(ctx.exception.gate, "publish")
        # readback
        state = self._state_dir()
        real_read = persistence._io.read_bytes
        def garbled_read(path, *a, **kw):
            data = real_read(path, *a, **kw)
            return b"garbage" if ".tmp." in str(path) else data
        with mock.patch.object(persistence._io, "read_bytes", garbled_read):
            with self.assertRaises(self._err()) as ctx:
                self._persist(state, enriched)
        self.assertEqual(ctx.exception.gate, "readback")

    def test_producer_errors_wrapped(self):
        """RED-11 (r7-m3): invalid enriched -> PersistenceError(preflight, invalid_input)."""
        state = self._state_dir()
        bad = {"source_evaluation_id": "eval_" + "a" * 64,
               "source_evaluation_snapshot": {"schema_version": 1}}
        with self.assertRaises(self._err()) as ctx:
            self._persist(state, bad)
        self.assertEqual(ctx.exception.gate, "preflight")
        self.assertEqual(ctx.exception.code, "invalid_input")
        self.assertNotIn("EligibilityInputError", type(ctx.exception).__name__)
        self.assertNotIn("ReflectionInputError", type(ctx.exception).__name__)

    def test_persistence_canonicalizer_sole_authority(self):
        """RED-11 (r6-M4): persistence must not import producer serializers for disk bytes."""
        import inspect
        from src.learning_loop import persistence
        source = inspect.getsource(persistence)
        for token in (
            "from src.learning_loop.reflection_builder import canonical_json",
            "reflection_builder.canonical_json",
            "eligibility._canonical_json",
            "queue_request._canonical_json",
        ):
            self.assertNotIn(token, source)
        self.assertIn("_canonical_json", source)

    def test_persistence_canonicalizer_golden_oracle(self):
        """RED-11 (⚙-10): persistence's private canonicalizer matches Appendix C vectors."""
        from src.learning_loop.persistence import _canonical_json
        for value, expected in (
            (0.3, "0.3"),
            (0.30000000000000004, "0.30000000000000004"),
            (1 / 3, "0.3333333333333333"),
            (-0.0, "-0.0"),
            (1e300, "1e+300"),
            (0.5, "0.5"),
            (0, "0"),
            (True, "true"),
            (False, "false"),
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(_canonical_json(value), expected)
        self.assertEqual(_canonical_json({"任务": "值"}), '{"\\u4efb\\u52a1":"\\u503c"}')
