"""S3.2 Evidence Composition — per-memory stratum classification and aggregation.

Pure functions only. No I/O, no LLM, no mutation.
"""

import hashlib
import json

# Frozen identity whitelist (Phase 5 v4.2.1 §2.3): only the semantic facts
# that determine the evaluation enter the ID. Derived fields (utility,
# sufficiency, verdict) and adapter metadata are excluded. Any future
# threshold that affects verdict/sufficiency MUST be added here in the
# same change that introduces it.
_IDENTITY_THRESHOLD_KEYS = ("utility_delta_min", "verified_ratio_min", "defined_before_run")


def _evaluation_identity(experiment, utility, summary, thresholds):
    payload = {
        "schema_version": 2,
        "experiment": {
            "task_id": experiment.get("task_id", ""),
            "without_memory_run_id": experiment.get("without_memory_run_id", ""),
            "with_memory_run_id": experiment.get("with_memory_run_id", ""),
        },
        "comparison": {
            "first_score": utility["first_score"],
            "second_score": utility["second_score"],
            "score_delta": utility["pack_utility_delta"],
        },
        "evidence_composition": {
            "verified": summary.get("verified", 0),
            "unknown": summary.get("unknown", 0),
            "contradicted": summary.get("contradicted", 0),
        },
        "thresholds": {
            key: thresholds.get(key) for key in _IDENTITY_THRESHOLD_KEYS
        },
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return "eval_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_memory_record(record):
    """Classify a memory record into a correctness stratum.

    Priority: contradicted > verified > unknown.

    Returns one of: "contradicted", "verified", "unknown".
    """
    if not isinstance(record, dict):
        raise ValueError("record must be a mapping")
    if record.get("status") in {"conflicted", "conflict"}:
        return "contradicted"
    superseded = record.get("superseded_by")
    if superseded:
        if isinstance(superseded, (list, tuple)) and len(superseded) > 0:
            return "contradicted"
        if isinstance(superseded, str) and superseded.strip():
            return "contradicted"
    if record.get("confidence") == "confirmed" and record.get("status") == "active":
        return "verified"
    return "unknown"


def build_memory_evidence_composition(records):
    """Build an evidence composition from a list of memory records.

    Returns a dict with per-memory stratum classification and aggregate summary.
    Does NOT produce utility, trust, weight, or ranking.
    """
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be a list")
    entries = []
    counts = {"verified": 0, "unknown": 0, "contradicted": 0}
    for record in records:
        memory_id = record.get("id", "")
        stratum = classify_memory_record(record)
        entries.append({"memory_id": memory_id, "stratum": stratum})
        counts[stratum] += 1
    return {
        "memory_entries": entries,
        "summary": {
            "total": len(entries),
            "verified": counts["verified"],
            "unknown": counts["unknown"],
            "contradicted": counts["contradicted"],
        },
    }


def evaluate_pack_utility(comparison):
    """Extract pack-level utility delta from a comparison artifact.

    Returns first_score, second_score, and pack_utility_delta.
    Does NOT produce per-memory attribution, ranking, or recommendation.
    """
    if not isinstance(comparison, dict):
        raise ValueError("comparison must be a mapping")
    first = comparison.get("first_score")
    second = comparison.get("second_score")
    delta = comparison.get("score_delta")
    if first is None or second is None or delta is None:
        raise ValueError("comparison must contain first_score, second_score, score_delta")
    return {
        "first_score": float(first),
        "second_score": float(second),
        "pack_utility_delta": float(delta),
    }


def check_evidence_sufficiency(composition, verified_ratio_min):
    """Check whether evidence composition meets the verified ratio threshold.

    Returns status ("sufficient"/"insufficient") and verified_ratio.
    Does NOT produce trust, quality, or confidence score.
    """
    if not isinstance(composition, dict):
        raise ValueError("composition must be a mapping")
    summary = composition.get("summary", {})
    total = summary.get("total", 0)
    verified = summary.get("verified", 0)
    if total <= 0:
        return {"status": "insufficient", "verified_ratio": 0.0}
    ratio = verified / total
    status = "sufficient" if ratio >= verified_ratio_min else "insufficient"
    return {"status": status, "verified_ratio": ratio}


def generate_validation_verdict(utility_delta, evidence_sufficient, thresholds):
    """Generate a validation verdict following the frozen evaluation order.

    Step 1: Validate threshold freeze
    Step 2: Check evidence sufficiency → C if insufficient
    Step 3: Compare delta to utility_delta_min → A if positive, B otherwise
    """
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be a mapping")
    if not thresholds.get("defined_before_run"):
        return {"validation_verdict": "C"}
    if not evidence_sufficient:
        return {"validation_verdict": "C"}
    utility_delta_min = thresholds.get("utility_delta_min", 0.0)
    if utility_delta > utility_delta_min:
        return {"validation_verdict": "A"}
    return {"validation_verdict": "B"}


def build_utility_evaluation(experiment, comparison, composition, thresholds):
    """Assemble a utility_evaluation artifact.

    Joins experiment, utility, evidence composition, and verdict into a
    single report. Does NOT produce trust, ranking, decision, or governance fields.
    """
    if not isinstance(experiment, dict):
        raise ValueError("experiment must be a mapping")
    if not isinstance(comparison, dict):
        raise ValueError("comparison must be a mapping")
    if not isinstance(composition, dict):
        raise ValueError("composition must be a mapping")
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be a mapping")

    task_id = experiment.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("experiment.task_id must be a non-empty string")
    comparison_task_id = comparison.get("task_id")
    if not isinstance(comparison_task_id, str) or not comparison_task_id:
        raise ValueError("comparison.task_id must be a non-empty string")
    if comparison_task_id != task_id:
        raise ValueError(
            f"experiment.task_id {task_id!r} does not match "
            f"comparison.task_id {comparison_task_id!r}"
        )

    summary_input = composition.get("summary", {})
    counts = {}
    for field in ("total", "verified", "unknown", "contradicted"):
        value = summary_input.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"composition summary.{field} must be a non-negative integer"
            )
        counts[field] = value
    if counts["total"] != counts["verified"] + counts["unknown"] + counts["contradicted"]:
        raise ValueError(
            "composition summary.total must equal verified + unknown + contradicted"
        )

    utility = evaluate_pack_utility(comparison)
    sufficiency = check_evidence_sufficiency(
        composition, thresholds.get("verified_ratio_min", 0.0)
    )
    verdict = generate_validation_verdict(
        utility["pack_utility_delta"],
        sufficiency["status"] == "sufficient",
        thresholds,
    )
    summary = composition.get("summary", {})
    return {
        "schema_version": 2,
        "evaluation_id": _evaluation_identity(experiment, utility, summary, thresholds),
        "experiment": {
            "task_id": experiment.get("task_id", ""),
            "with_memory_run_id": experiment.get("with_memory_run_id", ""),
            "without_memory_run_id": experiment.get("without_memory_run_id", ""),
        },
        "utility": {
            "pack_utility_delta": utility["pack_utility_delta"],
        },
        "evidence_composition": {
            "verified": summary.get("verified", 0),
            "unknown": summary.get("unknown", 0),
            "contradicted": summary.get("contradicted", 0),
        },
        "thresholds": dict(thresholds),
        "evidence_sufficiency": sufficiency,
        "validation_verdict": verdict["validation_verdict"],
    }


def export_utility_evaluation_md(evaluation):
    """Render a utility evaluation dict as a Markdown report.

    Returns a string suitable for display or storage.
    Does NOT write to disk. Does NOT introduce governance language.
    """
    if not isinstance(evaluation, dict):
        raise ValueError("evaluation must be a mapping")
    comp = evaluation.get("evidence_composition", {})
    utility = evaluation.get("utility", {})
    sufficiency = evaluation.get("evidence_sufficiency", {})
    verdict = evaluation.get("validation_verdict", "?")

    lines = [
        "# Utility Evaluation",
        "",
        "## Utility Signal",
        "",
        f"Pack utility delta: {utility.get('pack_utility_delta', 0.0)}",
        "",
        "## Evidence Composition",
        "",
        f"Verified: {comp.get('verified', 0)}",
        f"Unknown: {comp.get('unknown', 0)}",
        f"Contradicted: {comp.get('contradicted', 0)}",
        "",
        "## Evidence Sufficiency",
        "",
        "Status: " + sufficiency.get("status", "unknown"),
    ]
    verified_ratio = sufficiency.get("verified_ratio")
    if verified_ratio is not None:
        lines.append(f"Verified ratio: {verified_ratio:.3f}")
    lines.extend([
        "",
        "## Validation Verdict",
        "",
        str(verdict),
        "",
    ])
    return "\n".join(lines)
