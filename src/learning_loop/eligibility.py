"""S5.3 Eligibility Builder — deterministic FSM Matrix v1 classification.

Pure function. No I/O, no LLM, no new facts, no timestamps.
Contract: Phase 5 Design Baseline v4.3 §5 (eligibility matrix + preconditions),
Appendix B (reason literals, fsm_matrix_version=1),
Appendix C (canonicalizer_version=1).
"""

import hashlib
import json
import math
from collections import namedtuple


_FSMRow = namedtuple("_FSMRow", ("case_id", "verdict", "contradicted_gt0", "unknown_gt0", "status"))

FSM_MATRIX_V1 = (
    _FSMRow("case_01", "A", False, False, "eligible"),
    _FSMRow("case_02", "A", False, True, "eligible"),
    _FSMRow("case_03", "A", True, False, "eligible"),
    _FSMRow("case_04", "A", True, True, "eligible"),
    _FSMRow("case_05", "B", False, False, "eligible"),
    _FSMRow("case_06", "B", False, True, "eligible"),
    _FSMRow("case_07", "B", True, False, "eligible"),
    _FSMRow("case_08", "B", True, True, "eligible"),
    _FSMRow("case_09", "C", False, False, "ineligible"),
    _FSMRow("case_10", "C", False, True, "ineligible"),
    _FSMRow("case_11", "C", True, False, "ineligible"),
    _FSMRow("case_12", "C", True, True, "ineligible"),
)

_SCHEMA_VERSION = 1
_FSM_MATRIX_VERSION = 1


_REFLECTION_KEYS = frozenset((
    "schema_version", "template_version", "reflection_id",
    "source_evaluation_id", "source_snapshot_digest",
    "outcome_snapshot", "claims", "uncertainties",
    "missing_information", "non_conclusions",
))
_OUTCOME_KEYS = frozenset((
    "task_id", "without_memory_run_id", "with_memory_run_id",
    "validation_verdict", "pack_utility_delta",
    "evidence_composition", "evidence_sufficiency",
))
_COMPOSITION_KEYS = frozenset(("verified", "unknown", "contradicted"))
_SUFFICIENCY_KEYS = frozenset(("status", "verified_ratio"))
_ENRICHED_KEYS = frozenset(("source_evaluation_id", "source_evaluation_snapshot"))


class EligibilityInputError(ValueError):
    """Raised when input validation or gate precondition fails.

    Attributes:
        gate: str — the gate identifier ("shape", "source", "preconditions", "upstream")
        check: str | None — for gate:preconditions, the P1-P8 check that failed
    """

    def __init__(self, gate, message="", check=None):
        self.gate = gate
        self.check = check
        super().__init__(f"gate:{gate}" + (f":{check}" if check else "") + (f" {message}".rstrip() if message else ""))


def _canonical_json(value):
    """Canonical JSON per baseline appendix C (canonicalizer_version 1).

    Rules: sort_keys=True, separators=(",",":"), ensure_ascii=True,
    allow_nan=False, UTF-8, no BOM, no trailing newline.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def build_eligibility(reflection, enriched):
    """Build an eligibility artifact from a reflection and its source enriched evaluation.

    Pure function. Fail-closed gates: shape -> source -> preconditions -> upstream -> FSM.
    """
    _validate_shape(reflection, enriched)
    expected_reflection = _validate_source(enriched)
    _validate_preconditions(reflection, enriched)
    _validate_upstream(reflection, expected_reflection)
    return _fsm_lookup(reflection, enriched)


def _validate_upstream(reflection, expected_reflection):
    """gate:upstream — whole-object canonical comparison against trusted producer output.

    Only reaches here after shape/source/preconditions pass.
    """
    if _canonical_json(reflection) != _canonical_json(expected_reflection):
        raise EligibilityInputError(gate="upstream", message="reflection does not match expected bytes")


def _fsm_lookup(reflection, enriched):
    """FSM unique-row lookup + reason rendering + eligibility artifact."""
    outcome = reflection["outcome_snapshot"]
    comp = outcome["evidence_composition"]
    verdict = outcome["validation_verdict"]
    contradicted_gt0 = comp["contradicted"] > 0
    unknown_gt0 = comp["unknown"] > 0

    matches = [row for row in FSM_MATRIX_V1
               if row.verdict == verdict
               and row.contradicted_gt0 == contradicted_gt0
               and row.unknown_gt0 == unknown_gt0]
    if len(matches) != 1:
        msg = f"expected 1 FSM match for ({verdict},{contradicted_gt0},{unknown_gt0}), got {len(matches)}"
        raise RuntimeError(msg)

    matched_case = matches[0].case_id
    eligibility_status = matches[0].status

    # reasons — from verified facts, NOT from matched_case
    reasons = []
    # verdict reasons
    if verdict in ("A", "B"):
        reasons.append(f"R-{verdict}")
    else:
        source_snap = enriched["source_evaluation_snapshot"]
        frozen = source_snap["thresholds"]["defined_before_run"]
        status = outcome["evidence_sufficiency"]["status"]
        if not frozen:
            reasons.append("R-C1")
        if status == "insufficient":
            reasons.append("R-C2")
    if unknown_gt0:
        reasons.append("R-U")
    if contradicted_gt0:
        reasons.append("R-X")

    source_reflection_id = reflection["reflection_id"]
    eligibility_payload = {
        "schema_version": _SCHEMA_VERSION,
        "fsm_matrix_version": _FSM_MATRIX_VERSION,
        "source_reflection_id": source_reflection_id,
    }
    eligibility_id = "elig_" + hashlib.sha256(
        _canonical_json(eligibility_payload).encode("utf-8")
    ).hexdigest()

    return {
        "schema_version": _SCHEMA_VERSION,
        "eligibility_id": eligibility_id,
        "source_reflection_id": source_reflection_id,
        "fsm_matrix_version": _FSM_MATRIX_VERSION,
        "matched_case": matched_case,
        "eligibility_status": eligibility_status,
        "reasons": reasons,
    }


def _validate_shape(reflection, enriched):
    """gate:shape — structural shape only, no semantic validation."""
    if not isinstance(reflection, dict):
        raise EligibilityInputError(gate="shape", message="reflection must be a dict")
    if not isinstance(enriched, dict):
        raise EligibilityInputError(gate="shape", message="enriched must be a dict")
    if set(reflection) != _REFLECTION_KEYS:
        raise EligibilityInputError(gate="shape", message="reflection key set mismatch")
    if set(enriched) != _ENRICHED_KEYS:
        raise EligibilityInputError(gate="shape", message="enriched key set mismatch")

    outcome = reflection["outcome_snapshot"]
    if not isinstance(outcome, dict):
        raise EligibilityInputError(gate="shape", message="outcome_snapshot must be a dict")
    if set(outcome) != _OUTCOME_KEYS:
        raise EligibilityInputError(gate="shape", message="outcome_snapshot key set mismatch")

    composition = outcome["evidence_composition"]
    if not isinstance(composition, dict):
        raise EligibilityInputError(gate="shape", message="evidence_composition must be a dict")
    if set(composition) != _COMPOSITION_KEYS:
        raise EligibilityInputError(gate="shape", message="evidence_composition key set mismatch")

    sufficiency = outcome["evidence_sufficiency"]
    if not isinstance(sufficiency, dict):
        raise EligibilityInputError(gate="shape", message="evidence_sufficiency must be a dict")
    if set(sufficiency) != _SUFFICIENCY_KEYS:
        raise EligibilityInputError(gate="shape", message="evidence_sufficiency key set mismatch")

    for arr_field in ("claims", "uncertainties", "missing_information", "non_conclusions"):
        if not isinstance(reflection.get(arr_field), list):
            raise EligibilityInputError(gate="shape", message=f"{arr_field} must be a list")

    snap = enriched["source_evaluation_snapshot"]
    if not isinstance(snap, dict):
        raise EligibilityInputError(gate="shape", message="source_evaluation_snapshot must be a dict")


def _validate_source(enriched):
    """gate:source — validate enriched via S5.2 producer; convert to gate=source on failure.

    Returns the expected reflection (canonical, trusted) for upstream comparison.
    """
    from .reflection_builder import ReflectionInputError, build_reflection

    try:
        return build_reflection(enriched)
    except ReflectionInputError:
        msg = "enriched failed S5.2 re-admission validation"
        raise EligibilityInputError(gate="source", message=msg) from None


def _validate_preconditions(reflection, enriched):
    """gate:preconditions — P1-P8 independent checks on incoming reflection.

    Ordered: P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P8.
    Only reads reflection.outcome_snapshot + enriched.source_evaluation_snapshot.
    """
    outcome = reflection["outcome_snapshot"]
    source_snap = enriched["source_evaluation_snapshot"]

    # P1 — enum
    verdict = outcome["validation_verdict"]
    status = outcome["evidence_sufficiency"]["status"]
    if verdict not in ("A", "B", "C"):
        raise EligibilityInputError(gate="preconditions", check="P1", message=f"invalid verdict {verdict!r}")
    if status not in ("sufficient", "insufficient"):
        raise EligibilityInputError(gate="preconditions", check="P1", message=f"invalid status {status!r}")

    # P2 — non-negative int counts
    comp = outcome["evidence_composition"]
    for field in ("verified", "unknown", "contradicted"):
        val = comp[field]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise EligibilityInputError(gate="preconditions", check="P2", message=f"{field}={val!r}")

    # P3 — recompute ratio/status
    verified = comp["verified"]
    total = verified + comp["unknown"] + comp["contradicted"]
    ratio = outcome["evidence_sufficiency"]["verified_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise EligibilityInputError(gate="preconditions", check="P3", message=f"ratio {ratio!r} invalid")
    if not math.isfinite(ratio):
        raise EligibilityInputError(gate="preconditions", check="P3", message=f"ratio {ratio!r} not finite")
    expected_ratio = 0.0 if total == 0 else verified / total
    if _canonical_json(ratio) != _canonical_json(expected_ratio):
        raise EligibilityInputError(gate="preconditions", check="P3", message=f"ratio {ratio!r} != {expected_ratio!r}")
    threshold = source_snap["thresholds"]["verified_ratio_min"]
    if total == 0:
        expected_status = "insufficient"
    else:
        expected_status = "sufficient" if ratio >= threshold else "insufficient"
    if status != expected_status:
        raise EligibilityInputError(gate="preconditions", check="P3", message=f"status {status!r} != {expected_status!r}")

    # P4 — A/B domain precondition
    frozen = source_snap["thresholds"]["defined_before_run"]
    if verdict in ("A", "B") and (status != "sufficient" or not frozen):
        raise EligibilityInputError(gate="preconditions", check="P4", message="A/B requires sufficient+frozen")

    # P5 — A semantics
    delta = outcome["pack_utility_delta"]
    delta_min = source_snap["thresholds"]["utility_delta_min"]
    if verdict == "A" and not (delta > delta_min):
        raise EligibilityInputError(gate="preconditions", check="P5", message=f"A requires delta>{delta_min}")

    # P6 — B semantics
    if verdict == "B" and not (delta <= delta_min):
        raise EligibilityInputError(gate="preconditions", check="P6", message=f"B requires delta<={delta_min}")

    # P7 — C semantics
    if verdict == "C" and (frozen and status == "sufficient"):
        raise EligibilityInputError(gate="preconditions", check="P7", message="C requires not-frozen or insufficient")

    # P8 — snapshot projection consistency
    source_exp = source_snap["experiment"]
    pairs = (
        ("task_id", outcome["task_id"], source_exp["task_id"]),
        ("without_memory_run_id", outcome["without_memory_run_id"], source_exp["without_memory_run_id"]),
        ("with_memory_run_id", outcome["with_memory_run_id"], source_exp["with_memory_run_id"]),
        ("validation_verdict", outcome["validation_verdict"], source_snap["validation_verdict"]),
        ("pack_utility_delta", outcome["pack_utility_delta"], source_snap["utility"]["pack_utility_delta"]),
        ("verified", comp["verified"], source_snap["evidence_composition"]["verified"]),
        ("unknown", comp["unknown"], source_snap["evidence_composition"]["unknown"]),
        ("contradicted", comp["contradicted"], source_snap["evidence_composition"]["contradicted"]),
        ("status", outcome["evidence_sufficiency"]["status"], source_snap["evidence_sufficiency"]["status"]),
        ("verified_ratio", outcome["evidence_sufficiency"]["verified_ratio"], source_snap["evidence_sufficiency"]["verified_ratio"]),
    )
    for label, out_val, snap_val in pairs:
        if _canonical_json(out_val) != _canonical_json(snap_val):
            raise EligibilityInputError(gate="preconditions", check="P8",
                                        message=f"{label} projection mismatch: {out_val!r} != {snap_val!r}")
