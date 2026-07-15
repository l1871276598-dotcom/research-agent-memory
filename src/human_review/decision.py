"""Human Review Phase — decision.py pure-function layer (no I/O).

Frozen contract: Human Review design baseline v1.8 §1 (8-key decision schema),
Phase 5 baseline v4.3.2 appendix C (canonicalizer_version = 1).

HR.1: RED-0 (canonicalizer, dcd_ digest self-excluded, 8-key schema),
RED-1 (recorded_at explicit layered lexer, appendix P5).
"""

import hashlib
import json
import re


# §1 — 8-key closed decision schema.
DECISION_KEYS = frozenset({
    "schema_version",
    "review_queue_item_id",
    "decision_seq",
    "action",
    "operator_claim",
    "reason",
    "recorded_at",
    "decision_digest",
})

# §2 — evaluation disposition enum (full, no order, no default).
ACTIONS = frozenset({"accept", "reject", "defer", "revise"})

ERROR_CATEGORIES = {
    "invalid_review_chain": "jurisdiction",
    "request_not_found": "jurisdiction",
    "invalid_request": "precondition",
    "invalid_review_queue_item_id": "precondition",
    "invalid_decision_seq": "precondition",
    "invalid_action": "precondition",
    "invalid_operator_claim": "precondition",
    "invalid_reason": "precondition",
    "invalid_state_transition": "precondition",
    "invalid_state_dir": "precondition",
    "unsafe_path": "precondition",
    "path_too_long": "precondition",
    "decision_slot_conflict": "conflict",
    "malformed_directory_entry": "corruption",
    "malformed_decisions_namespace": "corruption",
    "malformed_review_queue_namespace": "corruption",
    "malformed_request": "corruption",
    "malformed_decision": "corruption",
    "orphan_decision": "corruption",
    "temp_byte_mismatch": "corruption",
    "temp_missing_before_link": "corruption",
    "decision_final_byte_mismatch": "corruption",
    "decision_final_missing": "corruption",
    "decision_directory_fsync_failed": "durability_unknown",
    "final_readback_failed": "durability_unknown",
    "precommit_directory_fsync_failed": "io",
    "path_read_failed": "io",
    "temp_write_failed": "io",
    "temp_read_failed": "io",
    "link_failed": "io",
    "mkdir_failed": "io",
    "clock_unavailable": "io",
}
ERROR_CODES = frozenset(ERROR_CATEGORIES.keys())

_RQ_ID = re.compile(r"^rq_[0-9a-f]{64}$")
_DCD = re.compile(r"^dcd_[0-9a-f]{64}$")


class DecisionSchemaError(Exception):
    """Raised when a decision object violates the frozen §1 schema."""


def canonical_bytes(obj):
    """Appendix C canonicalizer (version 1): sort_keys, compact separators,
    ensure_ascii, allow_nan=False, UTF-8 without BOM, no trailing newline."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def compute_digest(payload):
    """dcd_ digest = 'dcd_' + SHA-256(canonical bytes of the 7 non-digest keys).

    decision_digest is self-excluded from its own preimage.
    """
    body = {k: v for k, v in payload.items() if k != "decision_digest"}
    return "dcd_" + hashlib.sha256(canonical_bytes(body)).hexdigest()


def build_decision(review_queue_item_id, decision_seq, action, operator_claim,
                   reason, recorded_at):
    """Construct a valid 8-key decision object with a computed digest.

    Callers own field values; recorded_at lexical validity is enforced by
    validate_schema via validate_recorded_at.
    """
    payload = {
        "schema_version": 1,
        "review_queue_item_id": review_queue_item_id,
        "decision_seq": decision_seq,
        "action": action,
        "operator_claim": operator_claim,
        "reason": reason,
        "recorded_at": recorded_at,
    }
    payload["decision_digest"] = compute_digest(payload)
    validate_schema(payload)
    return payload


def validate_recorded_at(value):
    """Validate recorded_at lexical structure and calendar reality (RED-1).

    Must be exactly 20 characters in YYYY-MM-DDTHH:MM:SSZ format, using ASCII
    digits and a valid Gregorian calendar date/time.
    """
    if type(value) is not str:
        raise DecisionSchemaError("recorded_at must be a string")

    if len(value) != 20:
        raise DecisionSchemaError("recorded_at must be exactly 20 characters")

    if (value[4] != "-" or value[7] != "-" or value[10] != "T" or
        value[13] != ":" or value[16] != ":" or value[19] != "Z"):
        raise DecisionSchemaError("recorded_at structure template invalid")

    for i in (0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18):
        c = value[i]
        if not ("0" <= c <= "9"):
            raise DecisionSchemaError("recorded_at contains non-ASCII digits")

    year = int(value[0:4])
    month = int(value[5:7])
    day = int(value[8:10])
    hour = int(value[11:13])
    minute = int(value[14:16])
    second = int(value[17:19])

    if year < 1:
        raise DecisionSchemaError("year must be >= 1")
    if month < 1 or month > 12:
        raise DecisionSchemaError("month must be 1-12")

    if month in (1, 3, 5, 7, 8, 10, 12):
        max_day = 31
    elif month in (4, 6, 9, 11):
        max_day = 30
    else:
        is_leap = (year % 4 == 0) and (year % 100 != 0 or year % 400 == 0)
        max_day = 29 if is_leap else 28

    if day < 1 or day > max_day:
        raise DecisionSchemaError("day out of bounds")

    if hour > 23:
        raise DecisionSchemaError("hour must be 0-23")
    if minute > 59:
        raise DecisionSchemaError("minute must be 0-59")
    if second > 59:
        raise DecisionSchemaError("second must be 0-59")


def validate_schema(obj):
    """Validate an 8-key decision object against the frozen §1 schema.

    Fail-closed: raises DecisionSchemaError on any violation. bool is rejected
    where int is required (bool is an int subclass in Python).
    """
    if not isinstance(obj, dict):
        raise DecisionSchemaError("decision must be an object")

    keys = set(obj)
    if keys != set(DECISION_KEYS):
        missing = sorted(str(k) for k in set(DECISION_KEYS) - keys)
        extra = sorted(str(k) for k in keys - set(DECISION_KEYS))
        raise DecisionSchemaError(
            f"key set mismatch (missing={missing}, extra={extra})"
        )

    if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise DecisionSchemaError("schema_version must be int 1")

    rq_id = obj["review_queue_item_id"]
    if type(rq_id) is not str or _RQ_ID.fullmatch(rq_id) is None:
        raise DecisionSchemaError("invalid review_queue_item_id")

    if type(obj["decision_seq"]) is not int or obj["decision_seq"] not in (1, 2):
        raise DecisionSchemaError("decision_seq must be int 1 or 2")

    if type(obj["action"]) is not str or obj["action"] not in ACTIONS:
        raise DecisionSchemaError("invalid action")

    for field in ("operator_claim", "reason"):
        value = obj[field]
        if type(value) is not str or value == "":
            raise DecisionSchemaError(f"{field} must be a non-empty string")

    validate_recorded_at(obj["recorded_at"])

    digest = obj["decision_digest"]
    if type(digest) is not str or _DCD.fullmatch(digest) is None:
        raise DecisionSchemaError("invalid decision_digest")
    if digest != compute_digest(obj):
        raise DecisionSchemaError("decision_digest mismatch")


def reduce_state(events):
    """Reduce an iterable of decision events to a single string state (RED-2)."""
    try:
        events_list = list(events)
    except TypeError:
        raise DecisionSchemaError("reduce_state: events must be iterable")

    if not events_list:
        return "pending"

    by_seq = {}
    for ev in events_list:
        try:
            seq = ev.get("decision_seq")
            action = ev.get("action")
        except AttributeError:
            raise DecisionSchemaError("reduce_state: event must be a mapping")

        if type(seq) is not int or seq not in (1, 2):
            raise DecisionSchemaError(f"reduce_state: invalid seq {seq}")
        if type(action) is not str or action not in ACTIONS:
            raise DecisionSchemaError(f"reduce_state: unknown action {action}")
        if seq in by_seq:
            raise DecisionSchemaError(f"reduce_state: duplicate seq {seq}")
        by_seq[seq] = action

    if 1 not in by_seq:
        raise DecisionSchemaError("reduce_state: seq 2 present without seq 1")

    act1 = by_seq[1]

    if 2 in by_seq:
        act2 = by_seq[2]
        if act1 != "defer":
            raise DecisionSchemaError("reduce_state: seq 2 after terminal seq 1")
        if act2 == "defer":
            raise DecisionSchemaError("reduce_state: seq 2 cannot be defer")
        if act2 == "accept":
            return "accepted"
        if act2 == "reject":
            return "rejected"
        if act2 == "revise":
            return "revised"

    if act1 == "defer":
        return "deferred"
    if act1 == "accept":
        return "accepted"
    if act1 == "reject":
        return "rejected"
    if act1 == "revise":
        return "revised"

    raise DecisionSchemaError("reduce_state: invalid state combination")


def command_projection(decision):
    """Project a decision into a 5-tuple command representation (RED-3)."""
    return (
        decision["review_queue_item_id"],
        decision["decision_seq"],
        decision["action"],
        decision["operator_claim"],
        decision["reason"],
    )


def decode_event(raw, rq_id, seq):
    """Decode raw bytes into a validated decision dict (RED-10)."""
    if type(raw) is not bytes:
        raise DecisionSchemaError("canonical: raw must be bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise DecisionSchemaError("canonical: BOM rejected")
    try:
        def reject_duplicates(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError("duplicate key")
                d[k] = v
            return d
        def reject_constants(c):
            raise ValueError(f"illegal JSON constant {c}")
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates, parse_constant=reject_constants)
    except Exception as e:
        raise DecisionSchemaError(f"canonical: invalid JSON ({e})")

    try:
        cb = canonical_bytes(parsed)
    except Exception as e:
        raise DecisionSchemaError(f"canonical: canonicalization failed ({e})")

    if not isinstance(parsed, dict):
        raise DecisionSchemaError("canonical: top-level JSON must be an object")

    if cb != raw:
        raise DecisionSchemaError("canonical: non-canonical byte representation")

    validate_schema(parsed)

    if type(rq_id) is not str or _RQ_ID.fullmatch(rq_id) is None:
        raise DecisionSchemaError("binding: invalid review_queue_item_id")
    if type(seq) is not int or seq not in (1, 2):
        raise DecisionSchemaError("binding: invalid decision_seq")

    if parsed["review_queue_item_id"] != rq_id:
        raise DecisionSchemaError("binding: review_queue_item_id mismatch")
    if parsed["decision_seq"] != seq:
        raise DecisionSchemaError("binding: decision_seq mismatch")

    return parsed
