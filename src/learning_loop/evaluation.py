"""S3.5 Independent Evaluation Entry Point — bundle validation and orchestration.

Pure in-memory transformation: experiment bundle → utility_evaluation dict.
No I/O, no LLM, no MemoryStore, no Runtime coupling.
"""

import math

from .evidence import (
    build_memory_evidence_composition,
    build_utility_evaluation,
)


class EvaluationInputError(Exception):
    """Raised when the experiment bundle fails input validation."""


def evaluate_experiment_bundle(bundle):
    """Validate and evaluate an experiment bundle, returning a utility_evaluation dict.

    Validation order (frozen):
      1. Required fields
      2. Task identity
      3. Run identity
      4. Memory direction
      5. Score integrity
      6. Threshold range
      7. Evidence composition + utility evaluation

    Returns a dict with schema_version, experiment, thresholds, utility,
    memory_evidence_composition, evidence_sufficiency, validation_verdict,
    memory_record_source, and staleness_warning.
    """
    if not isinstance(bundle, dict):
        raise EvaluationInputError("bundle must be a mapping")

    # 1. Required fields
    required = (
        "experiment",
        "without_memory_outcome",
        "with_memory_outcome",
        "comparison",
        "memory_records",
        "thresholds",
    )
    for field in required:
        if field not in bundle:
            raise EvaluationInputError(f"bundle is missing required field: {field}")

    experiment = bundle["experiment"]
    without = bundle["without_memory_outcome"]
    with_ = bundle["with_memory_outcome"]
    comparison = bundle["comparison"]
    memory_records = bundle["memory_records"]
    thresholds = bundle["thresholds"]

    if not isinstance(experiment, dict):
        raise EvaluationInputError("experiment must be a mapping")
    if not isinstance(without, dict):
        raise EvaluationInputError("without_memory_outcome must be a mapping")
    if not isinstance(with_, dict):
        raise EvaluationInputError("with_memory_outcome must be a mapping")
    if not isinstance(comparison, dict):
        raise EvaluationInputError("comparison must be a mapping")
    if not isinstance(memory_records, (list, tuple)):
        raise EvaluationInputError("memory_records must be a list")
    if not isinstance(thresholds, dict):
        raise EvaluationInputError("thresholds must be a mapping")

    # 2. Task identity validation
    task_id = experiment.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise EvaluationInputError("experiment.task_id must be a non-empty string")
    comparison_task_id = comparison.get("task_id")
    if comparison_task_id != task_id:
        raise EvaluationInputError(
            f"comparison task_id {comparison_task_id!r} does not match "
            f"experiment task_id {task_id!r}"
        )

    # 3. Run identity validation
    first_run = comparison.get("first_run_id")
    second_run = comparison.get("second_run_id")
    exp_without = experiment.get("without_memory_run_id")
    exp_with = experiment.get("with_memory_run_id")

    if first_run != exp_without:
        raise EvaluationInputError(
            f"comparison first_run_id {first_run!r} does not match "
            f"experiment without_memory_run_id {exp_without!r}"
        )
    if second_run != exp_with:
        raise EvaluationInputError(
            f"comparison second_run_id {second_run!r} does not match "
            f"experiment with_memory_run_id {exp_with!r}"
        )
    if without.get("run_id") != first_run:
        raise EvaluationInputError(
            f"without_memory_outcome run_id {without.get('run_id')!r} does not "
            f"match comparison first_run_id {first_run!r}"
        )
    if with_.get("run_id") != second_run:
        raise EvaluationInputError(
            f"with_memory_outcome run_id {with_.get('run_id')!r} does not "
            f"match comparison second_run_id {second_run!r}"
        )

    # 4. Memory direction validation
    without_used = without.get("used_memory_ids")
    with_used = with_.get("used_memory_ids")
    if not isinstance(without_used, (list, tuple)):
        raise EvaluationInputError(
            "without_memory_outcome.used_memory_ids must be a list"
        )
    if not isinstance(with_used, (list, tuple)):
        raise EvaluationInputError(
            "with_memory_outcome.used_memory_ids must be a list"
        )
    if without_used:
        raise EvaluationInputError(
            "without_memory run must have empty used_memory_ids"
        )
    if not with_used:
        raise EvaluationInputError(
            "with_memory run must have non-empty used_memory_ids"
        )

    # 5. Score integrity validation
    def finite_score(container, field, name):
        if field not in container:
            raise EvaluationInputError(f"{name} is missing")
        value = container[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvaluationInputError(f"{name} must be a number")
        if not math.isfinite(value):
            raise EvaluationInputError(f"{name} must be a finite number")
        return float(value)

    without_score = finite_score(without, "score", "without_memory_outcome.score")
    with_score = finite_score(with_, "score", "with_memory_outcome.score")
    first_score = finite_score(comparison, "first_score", "comparison.first_score")
    second_score = finite_score(comparison, "second_score", "comparison.second_score")
    comparison_delta = finite_score(comparison, "score_delta", "comparison.score_delta")

    if abs(first_score - without_score) > 1e-9:
        raise EvaluationInputError(
            f"comparison first_score {first_score} does not match "
            f"without_memory_outcome score {without_score}"
        )
    if abs(second_score - with_score) > 1e-9:
        raise EvaluationInputError(
            f"comparison second_score {second_score} does not match "
            f"with_memory_outcome score {with_score}"
        )
    if abs(comparison_delta - (second_score - first_score)) > 1e-9:
        raise EvaluationInputError(
            f"comparison score_delta {comparison_delta} does not match "
            f"calculated delta {second_score - first_score}"
        )

    # 6. Threshold validation
    for field in ("utility_delta_min", "verified_ratio_min", "defined_before_run"):
        if field not in thresholds:
            raise EvaluationInputError(f"thresholds is missing required field: {field}")

    utility_delta_min = thresholds["utility_delta_min"]
    verified_ratio_min = thresholds["verified_ratio_min"]
    defined_before_run = thresholds["defined_before_run"]

    if isinstance(utility_delta_min, bool) or not isinstance(
        utility_delta_min, (int, float)
    ):
        raise EvaluationInputError("thresholds.utility_delta_min must be a number")
    if (
        isinstance(verified_ratio_min, bool)
        or not isinstance(verified_ratio_min, (int, float))
        or not 0 <= verified_ratio_min <= 1
    ):
        raise EvaluationInputError(
            "thresholds.verified_ratio_min must be a number between 0 and 1"
        )
    if not isinstance(defined_before_run, bool):
        raise EvaluationInputError(
            "thresholds.defined_before_run must be a bool"
        )

    # 7. Build evidence composition from memory records
    composition = build_memory_evidence_composition(memory_records)

    # 8. Build utility evaluation via evidence.py pure functions
    evaluation = build_utility_evaluation(
        experiment, comparison, composition, thresholds
    )

    # 9. Add evaluation-layer metadata
    evaluation["memory_record_source"] = "caller_provided"
    evaluation["staleness_warning"] = True

    return evaluation
