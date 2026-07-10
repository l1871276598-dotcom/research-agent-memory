"""S5.2 Reflection Builder — deterministic, copy-only reflection artifact.

Pure function. No I/O, no LLM, no new facts, no timestamps.
Contract: Phase 5 Design Baseline v4.3 §4 (re-admission §4.2.1),
Appendix A (template_version 1), Appendix C (canonicalizer_version 1).
"""

import hashlib
import json
import math
import re

SCHEMA_VERSION = 1
TEMPLATE_VERSION = 1

_EVAL_ID_PATTERN = re.compile(r"^eval_[0-9a-f]{64}$")
_SNAPSHOT_FIELDS = frozenset((
    "schema_version", "evaluation_id", "experiment", "utility",
    "evidence_composition", "thresholds", "evidence_sufficiency",
    "validation_verdict", "memory_record_source", "staleness_warning",
))
_FORBIDDEN_KEY_TERMS = (
    "trust", "ranking", "weight", "per_memory_utility",
    "recommendation", "remove", "delete",
)


class ReflectionInputError(Exception):
    """Raised when an enriched evaluation fails re-admission validation."""


def canonical_json(value):
    """Canonical JSON per baseline appendix C (canonicalizer_version 1)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _fail(gate, message):
    raise ReflectionInputError(f"{gate}: {message}")


def _require_finite_number(value, name, gate):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(gate, f"{name} must be a number")
    if not math.isfinite(value):
        _fail(gate, f"{name} must be a finite number")


def _scan_keys(node, gate):
    if isinstance(node, dict):
        for key, child in node.items():
            folded = str(key).casefold()
            for term in _FORBIDDEN_KEY_TERMS:
                if term in folded:
                    _fail(gate, f"forbidden term {term!r} in key {key!r}")
            _scan_keys(child, gate)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan_keys(item, gate)


def _validate_shape(enriched):
    gate = "gate:shape"
    if not isinstance(enriched, dict):
        _fail(gate, "enriched must be a mapping")
    if set(enriched) != {"source_evaluation_id", "source_evaluation_snapshot"}:
        _fail(gate, "enriched must carry exactly source_evaluation_id and source_evaluation_snapshot")
    if not isinstance(enriched["source_evaluation_snapshot"], dict):
        _fail(gate, "source_evaluation_snapshot must be a mapping")


def _validate_type_closure(snapshot):
    gate = "gate:type-closure"
    unknown = set(snapshot) - _SNAPSHOT_FIELDS
    if unknown:
        _fail(gate, f"unknown snapshot fields: {sorted(unknown)}")
    missing = _SNAPSHOT_FIELDS - set(snapshot)
    if missing:
        _fail(gate, f"missing snapshot fields: {sorted(missing)}")
    schema_version = snapshot["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 2:
        _fail(gate, "schema_version must be the integer 2")
    evaluation_id = snapshot["evaluation_id"]
    if not isinstance(evaluation_id, str) or not _EVAL_ID_PATTERN.match(evaluation_id):
        _fail(gate, "evaluation_id must match ^eval_[0-9a-f]{64}$")
    experiment = snapshot["experiment"]
    if not isinstance(experiment, dict):
        _fail(gate, "experiment must be a mapping")
    for field in ("task_id", "without_memory_run_id", "with_memory_run_id"):
        value = experiment.get(field)
        if not isinstance(value, str) or not value:
            _fail(gate, f"experiment.{field} must be a non-empty string")
    utility = snapshot["utility"]
    if not isinstance(utility, dict) or "pack_utility_delta" not in utility:
        _fail(gate, "utility.pack_utility_delta is required")
    _require_finite_number(utility["pack_utility_delta"], "utility.pack_utility_delta", gate)
    composition = snapshot["evidence_composition"]
    if not isinstance(composition, dict):
        _fail(gate, "evidence_composition must be a mapping")
    for field in ("verified", "unknown", "contradicted"):
        value = composition.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(gate, f"evidence_composition.{field} must be a non-negative integer")
    thresholds = snapshot["thresholds"]
    if not isinstance(thresholds, dict):
        _fail(gate, "thresholds must be a mapping")
    for field in ("utility_delta_min", "verified_ratio_min", "defined_before_run"):
        if field not in thresholds:
            _fail(gate, f"thresholds.{field} is required")
    _require_finite_number(thresholds["utility_delta_min"], "thresholds.utility_delta_min", gate)
    ratio_min = thresholds["verified_ratio_min"]
    _require_finite_number(ratio_min, "thresholds.verified_ratio_min", gate)
    if not 0 <= ratio_min <= 1:
        _fail(gate, "thresholds.verified_ratio_min must be within [0, 1]")
    if not isinstance(thresholds["defined_before_run"], bool):
        _fail(gate, "thresholds.defined_before_run must be a bool")
    sufficiency = snapshot["evidence_sufficiency"]
    if not isinstance(sufficiency, dict) or "status" not in sufficiency or "verified_ratio" not in sufficiency:
        _fail(gate, "evidence_sufficiency must carry status and verified_ratio")
    if not isinstance(snapshot["memory_record_source"], str):
        _fail(gate, "memory_record_source must be a string")
    if not isinstance(snapshot["staleness_warning"], bool):
        _fail(gate, "staleness_warning must be a bool")
    _scan_keys(snapshot, gate)
    try:
        return canonical_json(snapshot)
    except (TypeError, ValueError) as exc:
        _fail(gate, f"snapshot is not canonically serializable: {exc}")


def _validate_recompute(snapshot):
    gate = "gate:recompute"
    sufficiency = snapshot["evidence_sufficiency"]
    status = sufficiency["status"]
    if status not in ("sufficient", "insufficient"):
        _fail(gate, "evidence_sufficiency.status must be sufficient or insufficient")
    verdict = snapshot["validation_verdict"]
    if verdict not in ("A", "B", "C"):
        _fail(gate, "validation_verdict must be A, B, or C")
    composition = snapshot["evidence_composition"]
    verified = composition["verified"]
    total = verified + composition["unknown"] + composition["contradicted"]
    expected_ratio = 0.0 if total == 0 else verified / total
    ratio = sufficiency["verified_ratio"]
    if canonical_json(ratio) != canonical_json(expected_ratio):
        _fail(gate, f"verified_ratio {ratio!r} does not recompute to {expected_ratio!r}")
    thresholds = snapshot["thresholds"]
    if total == 0:
        expected_status = "insufficient"
    else:
        expected_status = (
            "sufficient" if expected_ratio >= thresholds["verified_ratio_min"] else "insufficient"
        )
    if status != expected_status:
        _fail(gate, f"status {status!r} does not recompute to {expected_status!r}")
    if not thresholds["defined_before_run"]:
        expected_verdict = "C"
    elif expected_status == "insufficient":
        expected_verdict = "C"
    elif snapshot["utility"]["pack_utility_delta"] > thresholds["utility_delta_min"]:
        expected_verdict = "A"
    else:
        expected_verdict = "B"
    if verdict != expected_verdict:
        _fail(gate, f"validation_verdict {verdict!r} does not recompute to {expected_verdict!r}")


def _validate_equations(enriched, snapshot):
    gate = "gate:equation:E1"
    if enriched["source_evaluation_id"] != snapshot["evaluation_id"]:
        _fail(gate, "enriched.source_evaluation_id does not equal snapshot.evaluation_id")


def _entry(claim_id, statement, ref_field, ref_value):
    return {"claim_id": claim_id, "statement": statement, ref_field: ref_value}


def build_reflection(enriched):
    """Build a reflection artifact dict from an enriched utility evaluation.

    Re-admission validation is fail-closed and ordered:
    shape -> type closure -> derived recomputation -> hard equations.
    Output is alias-free (canonical round-trip) and byte-replayable.
    """
    _validate_shape(enriched)
    snapshot_canonical = _validate_type_closure(enriched["source_evaluation_snapshot"])
    snapshot = json.loads(snapshot_canonical)
    _validate_recompute(snapshot)
    _validate_equations(enriched, snapshot)

    digest = "snap_" + hashlib.sha256(snapshot_canonical.encode("utf-8")).hexdigest()
    source_evaluation_id = snapshot["evaluation_id"]
    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "template_version": TEMPLATE_VERSION,
        "source_evaluation_id": source_evaluation_id,
        "source_snapshot_digest": digest,
    }
    reflection_id = "rf_" + hashlib.sha256(
        canonical_json(identity_payload).encode("utf-8")
    ).hexdigest()

    experiment = snapshot["experiment"]
    composition = snapshot["evidence_composition"]
    sufficiency = snapshot["evidence_sufficiency"]
    thresholds = snapshot["thresholds"]
    delta = snapshot["utility"]["pack_utility_delta"]
    token = canonical_json

    claims = [
        _entry("CL-1", f"pack_utility_delta is {token(delta)}.",
               "evidence_ref", "outcome_snapshot.pack_utility_delta"),
        _entry("CL-2", f"validation_verdict is {token(snapshot['validation_verdict'])}.",
               "evidence_ref", "outcome_snapshot.validation_verdict"),
        _entry("CL-3",
               "evidence composition is "
               f"verified={token(composition['verified'])}, "
               f"unknown={token(composition['unknown'])}, "
               f"contradicted={token(composition['contradicted'])}.",
               "evidence_ref", "outcome_snapshot.evidence_composition"),
        _entry("CL-4",
               f"evidence sufficiency is {token(sufficiency['status'])} "
               f"with verified_ratio {token(sufficiency['verified_ratio'])}.",
               "evidence_ref", "outcome_snapshot.evidence_sufficiency"),
        _entry("CL-5",
               "thresholds were "
               f"utility_delta_min={token(thresholds['utility_delta_min'])}, "
               f"verified_ratio_min={token(thresholds['verified_ratio_min'])}, "
               f"defined_before_run={token(thresholds['defined_before_run'])}.",
               "evidence_ref", "source_evaluation_snapshot.thresholds"),
    ]
    uncertainties = []
    if composition["unknown"] > 0:
        uncertainties.append(_entry(
            "U-1", f"{token(composition['unknown'])} memory record(s) are unverified.",
            "evidence_ref", "outcome_snapshot.evidence_composition.unknown"))
    if composition["contradicted"] > 0:
        uncertainties.append(_entry(
            "U-2", f"{token(composition['contradicted'])} memory record(s) are contradicted.",
            "evidence_ref", "outcome_snapshot.evidence_composition.contradicted"))
    if snapshot["staleness_warning"] is True:
        uncertainties.append(_entry(
            "U-3", "memory records may be stale; staleness_warning is true.",
            "evidence_ref", "source_evaluation_snapshot.staleness_warning"))
    missing_information = []
    if snapshot["memory_record_source"] == "caller_provided":
        missing_information.append(_entry(
            "M-1",
            "memory records were caller_provided and not re-verified from a run snapshot.",
            "evidence_ref", "source_evaluation_snapshot.memory_record_source"))
    non_conclusions = [
        _entry("N-1",
               "pack-level utility delta cannot attribute contribution to any individual memory record.",
               "contract_ref", "baseline-v4.3 §2"),
        _entry("N-2",
               "the validation verdict does not justify any memory lifecycle action.",
               "contract_ref", "baseline-v4.3 §2"),
        _entry("N-3",
               "evidence counts do not constitute reliability or weighting of memory records.",
               "contract_ref", "contracts/06 §3-exclusions"),
        _entry("N-4",
               "coverage between memory_records and used_memory_ids was not checked; "
               "no per-memory attribution is provided.",
               "contract_ref", "contracts/06 §2-not-gates"),
    ]

    reflection = {
        "schema_version": SCHEMA_VERSION,
        "template_version": TEMPLATE_VERSION,
        "reflection_id": reflection_id,
        "source_evaluation_id": source_evaluation_id,
        "source_snapshot_digest": digest,
        "outcome_snapshot": {
            "task_id": experiment["task_id"],
            "without_memory_run_id": experiment["without_memory_run_id"],
            "with_memory_run_id": experiment["with_memory_run_id"],
            "validation_verdict": snapshot["validation_verdict"],
            "pack_utility_delta": delta,
            "evidence_composition": {
                "verified": composition["verified"],
                "unknown": composition["unknown"],
                "contradicted": composition["contradicted"],
            },
            "evidence_sufficiency": {
                "status": sufficiency["status"],
                "verified_ratio": sufficiency["verified_ratio"],
            },
        },
        "claims": claims,
        "uncertainties": uncertainties,
        "missing_information": missing_information,
        "non_conclusions": non_conclusions,
    }

    # Output self-checks (E2/E3/E4): construction invariants, fail-closed.
    if reflection["source_evaluation_id"] != enriched["source_evaluation_id"]:
        _fail("gate:equation:E2", "reflection source id diverged")
    recomputed = "snap_" + hashlib.sha256(snapshot_canonical.encode("utf-8")).hexdigest()
    if reflection["source_snapshot_digest"] != recomputed:
        _fail("gate:equation:E3", "snapshot digest diverged")
    projection = reflection["outcome_snapshot"]
    for token_pair in (
        (projection["task_id"], experiment["task_id"]),
        (projection["pack_utility_delta"], snapshot["utility"]["pack_utility_delta"]),
        (projection["evidence_composition"], composition),
        (projection["evidence_sufficiency"], sufficiency),
    ):
        if canonical_json(token_pair[0]) != canonical_json(token_pair[1]):
            _fail("gate:equation:E4", "outcome projection diverged from snapshot")
    return reflection
