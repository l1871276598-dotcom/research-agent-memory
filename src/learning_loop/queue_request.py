"""S5.4 Human Review Queue request producer (baseline v4.3.1 §6/§8).

Pure function, no I/O. Only an *eligible* eligibility artifact may yield a
queue request; the request is immutable (status is always "pending") and its
identity payload is exactly {"source_eligibility_id": ...} so that schema or
template upgrades never change the request path (§8).
"""

import hashlib
import json

try:
    from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
except ImportError:  # pragma: no cover - direct src/ imports
    from learning_loop.eligibility import build_eligibility, EligibilityInputError

SCHEMA_VERSION = 1
TEMPLATE_VERSION = 1
DECISION_OPTIONS = ("accept", "reject", "defer", "revise")


class QueueRequestInputError(ValueError):
    """Raised when input validation or a gate precondition fails.

    Attributes:
        gate: str — "shape", "upstream", or "ineligible"
        check: str | None — optional finer-grained check identifier
    """

    def __init__(self, gate, message="", check=None):
        self.gate = gate
        self.check = check
        super().__init__(f"gate:{gate}" + (f":{check}" if check else "") + (f" {message}".rstrip() if message else ""))


def _canonical_json(value):
    """Canonical JSON per baseline appendix C (canonicalizer_version 1)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _token(value):
    """Appendix C interpolation token: the canonical JSON token of the value."""
    return _canonical_json(value)


def _deep_copy(value):
    """Deterministic alias-free copy via canonical round-trip."""
    return json.loads(_canonical_json(value))


def build_review_queue_request(eligibility, reflection, enriched):
    """Build an immutable review-queue request from an eligible chain.

    Pure function. Gates: shape -> upstream (byte comparison against the
    frozen producer chain) -> ineligible rejection.
    """
    if not isinstance(eligibility, dict) or not isinstance(reflection, dict) \
            or not isinstance(enriched, dict):
        raise QueueRequestInputError(gate="shape", message="inputs must be dicts")

    try:
        expected_eligibility = build_eligibility(reflection, enriched)
    except EligibilityInputError as exc:
        gate = "shape" if exc.gate == "shape" else "upstream"
        raise QueueRequestInputError(
            gate=gate, message=f"upstream rebuild failed at eligibility gate:{exc.gate}"
        ) from None

    if _canonical_json(eligibility) != _canonical_json(expected_eligibility):
        raise QueueRequestInputError(
            gate="upstream", message="eligibility does not match expected bytes"
        )

    if eligibility["eligibility_status"] != "eligible":
        raise QueueRequestInputError(
            gate="ineligible", message="ineligible chains carry no queue request"
        )

    snapshot = enriched["source_evaluation_snapshot"]
    outcome = reflection["outcome_snapshot"]
    experiment = _deep_copy(snapshot["experiment"])

    summary = (
        f"experiment {_token(experiment['task_id'])}: "
        f"runs {_token(experiment['without_memory_run_id'])} -> "
        f"{_token(experiment['with_memory_run_id'])}, "
        f"verdict {_token(outcome['validation_verdict'])}."
    )

    payload = _canonical_json({"source_eligibility_id": eligibility["eligibility_id"]})
    review_queue_item_id = "rq_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return {
        "schema_version": SCHEMA_VERSION,
        "template_version": TEMPLATE_VERSION,
        "review_queue_item_id": review_queue_item_id,
        "source_eligibility_id": eligibility["eligibility_id"],
        "source_evaluation_id": reflection["source_evaluation_id"],
        "source_snapshot_digest": reflection["source_snapshot_digest"],
        "experiment": experiment,
        "status": "pending",
        "summary": summary,
        "evidence_summary": {
            "validation_verdict": outcome["validation_verdict"],
            "pack_utility_delta": outcome["pack_utility_delta"],
            "verified": outcome["evidence_composition"]["verified"],
            "unknown": outcome["evidence_composition"]["unknown"],
            "contradicted": outcome["evidence_composition"]["contradicted"],
        },
        "missing_information": _deep_copy(reflection["missing_information"]),
        "decision_options": list(DECISION_OPTIONS),
    }
