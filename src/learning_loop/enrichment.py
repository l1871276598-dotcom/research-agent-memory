"""S5.1d Phase 5 enrichment — copy-only source evaluation snapshot.

Pure function only. No I/O, no LLM, no recomputation of Phase 4 facts.
The snapshot preserves the input utility_evaluation exactly so later
Reflection / Human Review artifacts can be audited against it.
"""

import json


def build_enriched_utility_evaluation(evaluation):
    """Build an enriched_utility_evaluation dict from a formal v2 evaluation.

    Copy-only: writes source_evaluation_id and an immutable
    source_evaluation_snapshot equal to the input. Does NOT recompute,
    drop, or add Phase 4 facts, and does NOT produce governance fields.
    """
    if not isinstance(evaluation, dict):
        raise ValueError("evaluation must be a mapping")
    if evaluation.get("schema_version") != 2:
        raise ValueError("evaluation must be a schema v2 utility_evaluation")
    evaluation_id = evaluation.get("evaluation_id")
    if not isinstance(evaluation_id, str) or not evaluation_id:
        raise ValueError("evaluation must carry a non-empty evaluation_id")
    try:
        canonical = json.dumps(
            evaluation, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"evaluation is not canonically serializable: {exc}") from exc
    return {
        "source_evaluation_id": evaluation_id,
        "source_evaluation_snapshot": json.loads(canonical),
    }
