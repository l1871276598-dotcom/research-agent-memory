"""S3.5 Independent Evaluation Entry Point — bundle validation and orchestration.

Pure in-memory transformation: experiment bundle → utility_evaluation dict.
No I/O, no LLM, no MemoryStore, no Runtime coupling.
"""

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
      2. Run identity
      3. Memory direction
      4. Score integrity
      5. Threshold range
      6. Evidence composition + utility evaluation

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

    # 2. Run identity validation
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

    # 3. Memory direction validation
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

    # 4. Score integrity validation
    calculated_delta = with_.get("score", 0) - without.get("score", 0)
    comparison_delta = comparison.get("score_delta", 0)
    if abs(comparison_delta - calculated_delta) > 1e-9:
        raise EvaluationInputError(
            f"comparison score_delta {comparison_delta} does not match "
            f"calculated delta {calculated_delta}"
        )

    # 5. Threshold validation
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

    # 6. Build evidence composition from memory records
    composition = build_memory_evidence_composition(memory_records)

    # 7. Build utility evaluation via evidence.py pure functions
    evaluation = build_utility_evaluation(
        experiment, comparison, composition, thresholds
    )

    # 8. Add evaluation-layer metadata
    evaluation["memory_record_source"] = "caller_provided"
    evaluation["staleness_warning"] = True

    return evaluation
