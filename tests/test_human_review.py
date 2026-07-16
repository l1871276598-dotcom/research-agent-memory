"""Human Review Phase tests.

HR.1 (decision.py pure functions). Increment 1 = RED-0 schema/canonicalizer/
digest. Later increments extend this file (recorded_at lexer, §5 decoder,
reduction, projection).
"""

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from human_review.decision import (  # noqa: E402
    ACTIONS,
    DECISION_KEYS,
    DecisionSchemaError,
    build_decision,
    canonical_bytes,
    compute_digest,
    validate_schema,
)


RQ_ID = "rq_" + "a" * 64
RECORDED_AT = "2026-07-11T00:00:00Z"


def valid_decision(**overrides):
    """A schema-valid decision with a correctly computed digest."""
    d = build_decision(
        review_queue_item_id=RQ_ID,
        decision_seq=1,
        action="accept",
        operator_claim="looks correct",
        reason="evidence sufficient",
        recorded_at=RECORDED_AT,
    )
    d.update(overrides)
    return d


class CanonicalizerTests(unittest.TestCase):
    def test_golden_bytes_sort_compact_ensure_ascii(self):
        # sort_keys, no whitespace separators, non-ASCII -> \uXXXX escape.
        self.assertEqual(
            canonical_bytes({"b": 2, "a": "x", "c": "文"}),
            b'{"a":"x","b":2,"c":"\\u6587"}',
        )

    def test_no_trailing_newline(self):
        self.assertFalse(canonical_bytes({"a": 1}).endswith(b"\n"))

    def test_rejects_nan(self):
        with self.assertRaises(ValueError):
            canonical_bytes({"x": float("nan")})


class DigestTests(unittest.TestCase):
    def test_digest_prefix_and_length(self):
        d = valid_decision()
        self.assertTrue(d["decision_digest"].startswith("dcd_"))
        self.assertEqual(len(d["decision_digest"]), 4 + 64)

    def test_digest_self_excluded(self):
        # Recomputing over an object with any decision_digest value must be
        # stable, because decision_digest is excluded from its own preimage.
        d = valid_decision()
        expected = d["decision_digest"]
        tampered = dict(d, decision_digest="dcd_" + "0" * 64)
        self.assertEqual(compute_digest(tampered), expected)

    def test_digest_covers_the_seven_keys(self):
        base = valid_decision()
        changed = valid_decision(action="reject")
        # action differs -> digest preimage differs -> different digest.
        self.assertNotEqual(
            compute_digest(base), compute_digest(changed)
        )


class SchemaValidationTests(unittest.TestCase):
    def test_valid_decision_passes(self):
        validate_schema(valid_decision())  # must not raise

    def test_eight_keys_frozen(self):
        self.assertEqual(len(DECISION_KEYS), 8)

    def test_missing_key_fails(self):
        d = valid_decision()
        del d["reason"]
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)

    def test_extra_key_fails(self):
        d = valid_decision(unexpected="x")
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)

    def test_heterogeneous_extra_keys_fails(self):
        d = valid_decision()
        d["unexpected"] = "x"
        d[123] = "y"  # Int key
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)

    def test_not_an_object_fails(self):
        for bad in ([], "x", 1, None):
            with self.assertRaises(DecisionSchemaError):
                validate_schema(bad)

    def test_schema_version_exact_int_one(self):
        for bad in (True, "1", 1.0, 2, 0):
            with self.assertRaises(DecisionSchemaError):
                validate_schema(_rebuilt(valid_decision(), schema_version=bad))

    def test_decision_seq_literal_one_or_two(self):
        for good in (1, 2):
            validate_schema(_rebuilt(valid_decision(), decision_seq=good))
        for bad in (True, "1", 1.0, 3, 0):
            with self.assertRaises(DecisionSchemaError):
                validate_schema(_rebuilt(valid_decision(), decision_seq=bad))

    def test_action_enum(self):
        for good in ACTIONS:
            validate_schema(_rebuilt(valid_decision(), action=good))
        for bad in ("Accept", "", "approve", 1, None):
            with self.assertRaises(DecisionSchemaError):
                validate_schema(_rebuilt(valid_decision(), action=bad))

    def test_claim_and_reason_nonempty_string(self):
        for field in ("operator_claim", "reason"):
            validate_schema(_rebuilt(valid_decision(), **{field: "ok"}))
            for bad in ("", 1, None, [], True):
                with self.assertRaises(DecisionSchemaError):
                    validate_schema(_rebuilt(valid_decision(), **{field: bad}))

    def test_rq_id_type_and_regex(self):
        for bad in (
            "RQ_" + "a" * 64,          # wrong prefix case
            "rq_" + "a" * 63,          # too short
            "rq_" + "a" * 65,          # too long
            "rq_" + "A" * 64,          # uppercase hex
            "rq_" + "g" * 64,          # non-hex
            "rq_" + "a" * 64 + " ",    # trailing space
            "rq_" + "a" * 64 + "\n",   # trailing newline
            123,
            None,
        ):
            with self.assertRaises(DecisionSchemaError):
                validate_schema(_rebuilt(valid_decision(), review_queue_item_id=bad))

    def test_decision_digest_recompute_and_format(self):
        # Wrong digest value fails.
        d = valid_decision()
        d["decision_digest"] = "dcd_" + "0" * 64
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)

        # Trailing newline on digest fails.
        d = valid_decision()
        d["decision_digest"] = d["decision_digest"] + "\n"
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)

        # Malformed digest string fails.
        for bad in ("dcd_" + "a" * 63, "xcd_" + "a" * 64, "a" * 64, 1, None):
            d = valid_decision()
            d["decision_digest"] = bad
            with self.assertRaises(DecisionSchemaError):
                validate_schema(d)

    def test_string_fields_exact_type(self):
        class MyStr(str): pass

        with self.assertRaises(DecisionSchemaError):
            validate_schema(_rebuilt(valid_decision(), review_queue_item_id=MyStr(RQ_ID)))

        with self.assertRaises(DecisionSchemaError):
            validate_schema(_rebuilt(valid_decision(), action=MyStr("accept")))

        with self.assertRaises(DecisionSchemaError):
            validate_schema(_rebuilt(valid_decision(), operator_claim=MyStr("claim")))

        with self.assertRaises(DecisionSchemaError):
            validate_schema(_rebuilt(valid_decision(), reason=MyStr("reason")))

        d = valid_decision()
        d["decision_digest"] = MyStr(d["decision_digest"])
        with self.assertRaises(DecisionSchemaError):
            validate_schema(d)


def _rebuilt(decision, **overrides):
    """Return a decision with fields overridden and the digest recomputed,
    so that only the field-under-test (not a stale digest) triggers failure.

    For the digest-invalidating fields themselves, recomputation keeps the
    digest self-consistent; validation still fails on the field's own rule.
    """
    payload = {k: v for k, v in decision.items() if k != "decision_digest"}
    payload.update(overrides)
    payload["decision_digest"] = compute_digest(payload)
    return payload


class RecordedAtLexerTests(unittest.TestCase):
    """RED-1 — recorded_at explicit layered lexer (appendix P5).

    Frozen legal form: ``YYYY-MM-DDTHH:MM:SSZ`` — exactly 20 chars, ASCII
    digits, uppercase T and Z, real Gregorian calendar (leap-year 02-29
    legal). Layered: type -> length 20 -> structure template -> ASCII digit
    domain -> calendar reality. NOT strptime-based. Drift/skew not detected
    (a well-formed real timestamp is accepted regardless of drift).

    Gemini implements ``human_review.decision.validate_recorded_at`` (raises
    DecisionSchemaError on any violation). Imported locally so RED-0 stays
    green while this group is RED until GREEN lands.
    """

    def _validate(self, value):
        from human_review.decision import validate_recorded_at
        return validate_recorded_at(value)

    def test_legal_vectors_pass(self):
        for good in (
            "2026-07-13T00:00:00Z",
            "2024-02-29T23:59:59Z",   # leap year Feb 29 is legal
            "2000-02-29T12:00:00Z",   # divisible-by-400 century leap year
            "2026-12-31T23:59:59Z",
        ):
            self._validate(good)  # must not raise

    def test_non_string_type_rejected(self):
        for bad in (None, 123, 1.0, True, [], {"a": 1}):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_length_not_twenty_rejected(self):
        for bad in (
            "2026-07-13T00:00:00",       # 19, missing Z
            "2026-07-13T00:00:000Z",     # 21
            "",
        ):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_structure_template_rejected(self):
        for bad in (
            "2026-07-13 00:00:00Z",      # space instead of T
            "2026/07/13T00:00:00Z",      # wrong date separators
            "2026-07-13T00-00-00Z",      # wrong time separators
        ):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_lowercase_t_or_z_rejected(self):
        for bad in ("2026-07-13t00:00:00Z", "2026-07-13T00:00:00z"):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_non_ascii_digits_rejected(self):
        for bad in (
            "2026-07-13T00:00:0０Z",   # fullwidth zero
            "2026-07-13T00:00:0٠Z",   # arabic-indic zero
        ):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_whitespace_in_field_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._validate("2026-07-13T00:00:0 Z")   # space in seconds

    def test_offset_rejected(self):
        for bad in ("2026-07-13T00:00:00+00:00", "2026-07-13T00:00:00+0000"):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_fractional_seconds_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._validate("2026-07-13T00:00:00.5Z")

    def test_leap_second_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._validate("2026-07-13T00:00:60Z")   # :60 seconds

    def test_out_of_range_fields_rejected(self):
        for bad in (
            "2026-00-13T00:00:00Z",      # month 00
            "2026-13-13T00:00:00Z",      # month 13
            "2026-07-00T00:00:00Z",      # day 00
            "2026-02-30T00:00:00Z",      # Feb 30
            "2026-07-13T24:00:00Z",      # hour 24
            "2026-07-13T00:60:00Z",      # minute 60
        ):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_non_leap_feb_29_rejected(self):
        for bad in (
            "2023-02-29T00:00:00Z",      # 2023 not a leap year
            "2100-02-29T00:00:00Z",      # century non-leap (not /400)
        ):
            with self.assertRaises(DecisionSchemaError):
                self._validate(bad)

    def test_year_zero_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._validate("0000-07-13T00:00:00Z")


class StateReductionTests(unittest.TestCase):
    """RED-2 — reduction over decisions (appendix P4, eight legal rows).

    Gemini implements ``reduce_state(events) -> str`` where events is an
    iterable of decision dicts for one rq. Returns exactly one of
    pending|deferred|accepted|rejected|revised. Order-independent; no partial
    state; fail-closed (raise DecisionSchemaError) on any illegal combination.
    """

    def _reduce(self, events):
        from human_review.decision import reduce_state
        return reduce_state(events)

    def _ev(self, seq, action):
        """A decision-shaped event carrying (decision_seq, action). Digest not
        recomputed — reduce_state consumes seq/action, not the digest."""
        base = build_decision(RQ_ID, 1, "accept", "c", "r", RECORDED_AT)
        return dict(base, decision_seq=seq, action=action)

    def test_eight_legal_rows(self):
        cases = [
            ([], "pending"),
            ([self._ev(1, "defer")], "deferred"),
            ([self._ev(1, "accept")], "accepted"),
            ([self._ev(1, "reject")], "rejected"),
            ([self._ev(1, "revise")], "revised"),
            ([self._ev(1, "defer"), self._ev(2, "accept")], "accepted"),
            ([self._ev(1, "defer"), self._ev(2, "reject")], "rejected"),
            ([self._ev(1, "defer"), self._ev(2, "revise")], "revised"),
        ]
        for events, expected in cases:
            self.assertEqual(self._reduce(events), expected)

    def test_order_independent(self):
        forward = [self._ev(1, "defer"), self._ev(2, "revise")]
        self.assertEqual(self._reduce(list(reversed(forward))), "revised")

    def test_unknown_action_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce([self._ev(1, "frobnicate")])

    def test_seq2_without_seq1_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce([self._ev(2, "accept")])

    def test_terminal_then_seq2_rejected(self):
        for terminal in ("accept", "reject", "revise"):
            with self.assertRaises(DecisionSchemaError):
                self._reduce([self._ev(1, terminal), self._ev(2, "accept")])

    def test_seq2_defer_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce([self._ev(1, "defer"), self._ev(2, "defer")])

    def test_duplicate_seq_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce([self._ev(1, "accept"), self._ev(1, "reject")])

    def test_illegal_seq_rejected(self):
        for bad_seq in (3, 0, 1.0, 2.0, True, False, "1", None):
            with self.assertRaises(DecisionSchemaError):
                self._reduce([self._ev(bad_seq, "accept")])

        # Also test with a valid seq 1 plus illegal seq
        for bad_seq in (3, 0, 1.0, 2.0, True, False, "1", None):
            with self.assertRaises(DecisionSchemaError):
                self._reduce([self._ev(1, "defer"), self._ev(bad_seq, "accept")])

    def test_non_mapping_event_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce([123])
        with self.assertRaises(DecisionSchemaError):
            self._reduce(["not a mapping"])

    def test_non_iterable_input_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._reduce(None)
        with self.assertRaises(DecisionSchemaError):
            self._reduce(123)


class SemanticProjectionTests(unittest.TestCase):
    """RED-3 — command semantic projection, exact code-point equality.

    Gemini implements ``command_projection(decision) -> tuple`` =
    (review_queue_item_id, decision_seq, action, operator_claim, reason).
    Equality is byte/code-point exact — NO trim, case-fold, or NFC/NFD.
    """

    def _proj(self, decision):
        from human_review.decision import command_projection
        return command_projection(decision)

    def test_projection_is_the_five_command_fields(self):
        d = valid_decision()
        self.assertEqual(
            self._proj(d),
            (d["review_queue_item_id"], d["decision_seq"], d["action"],
             d["operator_claim"], d["reason"]),
        )

    def test_recorded_at_and_digest_not_in_projection(self):
        # Same command fields, different recorded_at -> equal projection.
        a = build_decision(RQ_ID, 1, "accept", "claim", "reason",
                           "2026-07-13T00:00:00Z")
        b = build_decision(RQ_ID, 1, "accept", "claim", "reason",
                           "2026-07-14T23:59:59Z")
        self.assertEqual(self._proj(a), self._proj(b))

    def test_whitespace_difference_is_not_equal(self):
        a = build_decision(RQ_ID, 1, "accept", "claim", "reason", RECORDED_AT)
        b = build_decision(RQ_ID, 1, "accept", "claim ", "reason", RECORDED_AT)
        self.assertNotEqual(self._proj(a), self._proj(b))

    def test_case_difference_is_not_equal(self):
        a = build_decision(RQ_ID, 1, "accept", "Claim", "reason", RECORDED_AT)
        b = build_decision(RQ_ID, 1, "accept", "claim", "reason", RECORDED_AT)
        self.assertNotEqual(self._proj(a), self._proj(b))

    def test_nfc_nfd_difference_is_not_equal(self):
        # "é" composed (U+00E9) vs decomposed ("e" + U+0301) must differ.
        a = build_decision(RQ_ID, 1, "accept", "café", "reason", RECORDED_AT)
        b = build_decision(RQ_ID, 1, "accept", "café", "reason", RECORDED_AT)
        self.assertNotEqual(self._proj(a), self._proj(b))


class Section5DecoderTests(unittest.TestCase):
    """RED-10 — §5 five-layer decoder (bytes -> validated event).

    Gemini implements ``decode_event(raw, rq_id, seq) -> dict`` (raw: bytes;
    rq_id: str; seq: int). Layers, short-circuit in order:
      1. parse + canonical (raw == canonical_bytes(parsed); reject non-UTF-8,
         BOM, trailing whitespace, key reorder, duplicate keys)
      2. 8-key closed schema
      3. type/value domain (incl full recorded_at lexer)
      4. digest recompute
      5. filename binding (embedded rq_id/decision_seq == given rq_id/seq)
    Any failure raises DecisionSchemaError; the message names the failing
    layer so short-circuit (earliest layer wins) is assertable.
    """

    def _decode(self, raw, rq_id=RQ_ID, seq=1):
        from human_review.decision import decode_event
        return decode_event(raw, rq_id, seq)

    def _good_raw(self, seq=1):
        return canonical_bytes(
            build_decision(RQ_ID, seq, "accept", "c", "r", RECORDED_AT))

    def test_valid_canonical_bytes_decode(self):
        raw = self._good_raw()
        event = self._decode(raw)
        self.assertEqual(canonical_bytes(event), raw)

    def test_non_utf8_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._decode(b"\xff\xfe not utf-8")

    def test_bom_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._decode(b"\xef\xbb\xbf" + self._good_raw())

    def test_trailing_whitespace_non_canonical_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._decode(self._good_raw() + b"\n")

    def test_inserted_whitespace_non_canonical_rejected(self):
        with self.assertRaises(DecisionSchemaError):
            self._decode(b"{ " + self._good_raw()[1:])

    def test_duplicate_keys_rejected(self):
        # json.loads collapses duplicate keys; the decoder must reject them
        # (object_pairs_hook), not silently accept the last value.
        raw = b'{"action":"accept","action":"reject"}'
        with self.assertRaises(DecisionSchemaError):
            self._decode(raw)

    def test_eight_key_layer_rejects_missing_key(self):
        d = valid_decision()
        del d["reason"]
        # rebuild canonical bytes of the 7-key object
        raw = canonical_bytes(d)
        with self.assertRaises(DecisionSchemaError):
            self._decode(raw)

    def test_digest_layer_rejects_wrong_digest(self):
        d = valid_decision()
        tampered = dict(d, decision_digest="dcd_" + "0" * 64)
        with self.assertRaises(DecisionSchemaError):
            self._decode(canonical_bytes(tampered))

    def test_filename_binding_rq_id_mismatch(self):
        raw = self._good_raw()
        other = "rq_" + "b" * 64
        with self.assertRaises(DecisionSchemaError):
            self._decode(raw, rq_id=other, seq=1)

    def test_filename_binding_seq_mismatch(self):
        raw = self._good_raw(seq=1)
        with self.assertRaises(DecisionSchemaError):
            self._decode(raw, rq_id=RQ_ID, seq=2)

    def test_filename_binding_float_seq_rejected(self):
        raw = self._good_raw(seq=1)
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw, rq_id=RQ_ID, seq=1.0)
        self.assertIn("binding: invalid decision_seq", str(ctx.exception).lower())

    def test_filename_binding_bool_seq_rejected(self):
        raw = self._good_raw(seq=1)
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw, rq_id=RQ_ID, seq=True)
        self.assertIn("binding: invalid decision_seq", str(ctx.exception).lower())

    def test_filename_binding_subclass_rq_id_rejected(self):
        class MyStr(str):
            pass
        raw = self._good_raw()
        other = MyStr(RQ_ID)
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw, rq_id=other, seq=1)
        self.assertIn("binding: invalid review_queue_item_id", str(ctx.exception).lower())

    def test_filename_binding_invalid_format_rq_id_rejected(self):
        raw = self._good_raw()
        other = RQ_ID.upper()
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw, rq_id=other, seq=1)
        self.assertIn("binding: invalid review_queue_item_id", str(ctx.exception).lower())

    def test_filename_binding_rq_id_trailing_newline_fails(self):
        raw = self._good_raw()
        other = RQ_ID + "\n"
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw, rq_id=other, seq=1)
        self.assertIn("binding: invalid review_queue_item_id", str(ctx.exception).lower())

    def test_filename_binding_out_of_bounds_seq_rejected(self):
        raw = self._good_raw(seq=1)
        for bad_seq in (0, 3):
            with self.assertRaises(DecisionSchemaError) as ctx:
                self._decode(raw, rq_id=RQ_ID, seq=bad_seq)
            self.assertIn("binding: invalid decision_seq", str(ctx.exception).lower())

    def test_short_circuit_layer1_before_digest(self):
        # Non-canonical (layer 1) AND wrong digest (layer 4): layer 1 wins.
        d = dict(valid_decision(), decision_digest="dcd_" + "0" * 64)
        raw = canonical_bytes(d) + b"\n"   # trailing newline -> non-canonical
        with self.assertRaises(DecisionSchemaError) as ctx:
            self._decode(raw)
        self.assertIn("canonical", str(ctx.exception).lower())

    def test_nan_infinity_rejected(self):
        for raw in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}'):
            with self.assertRaises(DecisionSchemaError) as ctx:
                self._decode(raw)
            self.assertIn("canonical", str(ctx.exception).lower())

    def test_top_level_non_object_rejected(self):
        for raw in (b"1", b"[]", b'"str"', b"true", b"null"):
            with self.assertRaises(DecisionSchemaError) as ctx:
                self._decode(raw)
            self.assertIn("canonical", str(ctx.exception).lower())


class ErrorCodeTableTests(unittest.TestCase):
    """32-code frozen constant table (⚙-6). decision.py declares ERROR_CODES
    (frozenset) and ERROR_CATEGORIES (code -> category); concurrent_change is
    reserved (not returnable)."""

    EXPECTED = {
        "jurisdiction": {"invalid_review_chain", "request_not_found"},
        "precondition": {
            "invalid_request", "invalid_review_queue_item_id",
            "invalid_decision_seq", "invalid_action", "invalid_operator_claim",
            "invalid_reason", "invalid_state_transition", "invalid_state_dir",
            "unsafe_path", "path_too_long",
        },
        "conflict": {"decision_slot_conflict"},
        "corruption": {
            "malformed_directory_entry", "malformed_decisions_namespace",
            "malformed_review_queue_namespace", "malformed_request",
            "malformed_decision", "orphan_decision", "temp_byte_mismatch",
            "temp_missing_before_link", "decision_final_byte_mismatch",
            "decision_final_missing",
        },
        "durability_unknown": {
            "decision_directory_fsync_failed", "final_readback_failed",
        },
        "io": {
            "precommit_directory_fsync_failed", "path_read_failed",
            "temp_write_failed", "temp_read_failed", "link_failed",
            "mkdir_failed", "clock_unavailable",
        },
    }

    def _mod(self):
        from human_review import decision
        return decision

    def test_error_codes_equals_frozen_set_of_32(self):
        all_codes = set().union(*self.EXPECTED.values())
        self.assertEqual(len(all_codes), 32)
        self.assertEqual(set(self._mod().ERROR_CODES), all_codes)

    def test_error_codes_is_frozenset(self):
        self.assertIsInstance(self._mod().ERROR_CODES, frozenset)

    def test_category_counts(self):
        cats = self._mod().ERROR_CATEGORIES
        for category, codes in self.EXPECTED.items():
            got = {c for c, cat in cats.items() if cat == category}
            self.assertEqual(got, codes)

    def test_clock_unavailable_present_e1(self):
        self.assertIn("clock_unavailable", self._mod().ERROR_CODES)

    def test_concurrent_change_reserved_not_returnable(self):
        self.assertNotIn("concurrent_change", self._mod().ERROR_CODES)


class HR2Fixture(unittest.TestCase):
    """Real frozen producer-chain fixture for HR.2 store tests."""

    def _state_dir(self):
        import shutil
        import tempfile
        state = tempfile.mkdtemp(prefix="hr2-state-")
        self.addCleanup(shutil.rmtree, state, True)
        return state

    def _bundle(self):
        return {
            "experiment": {
                "task_id": "task-hr2",
                "without_memory_run_id": "run-a",
                "with_memory_run_id": "run-b",
            },
            "without_memory_outcome": {
                "run_id": "run-a", "score": 0.2, "used_memory_ids": [],
            },
            "with_memory_outcome": {
                "run_id": "run-b", "score": 0.5,
                "used_memory_ids": ["memory-1"],
            },
            "comparison": {
                "task_id": "task-hr2",
                "first_run_id": "run-a", "second_run_id": "run-b",
                "first_score": 0.2, "second_score": 0.5,
                "score_delta": 0.3,
            },
            "memory_records": [{
                "id": "memory-1", "confidence": "confirmed",
                "status": "active", "superseded_by": [],
            }],
            "thresholds": {
                "utility_delta_min": 0.1,
                "verified_ratio_min": 0.5,
                "defined_before_run": True,
            },
        }

    def _published_chain(self):
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.enrichment import build_enriched_utility_evaluation
        from src.learning_loop.persistence import persist_learning_chain

        enriched = build_enriched_utility_evaluation(
            evaluate_experiment_bundle(self._bundle())
        )
        state = self._state_dir()
        result = persist_learning_chain(state, enriched)
        return state, result["review_queue_request"]

    def _builder(self):
        from src.learning_loop.queue_request import build_review_queue_request
        return build_review_queue_request

    def _store(self, state, **kwargs):
        from human_review.review_store import ReviewStore
        return ReviewStore(
            state,
            forbidden_roots=kwargs.pop("forbidden_roots", ()),
            rq_builder=self._builder(),
            **kwargs,
        )

    def _tree_bytes(self, state):
        from pathlib import Path
        return {
            str(path.relative_to(state)): path.read_bytes()
            for path in sorted(Path(state).rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def _assert_code(self, expected, callable_):
        from human_review.review_store import ReviewError
        with self.assertRaises(ReviewError) as ctx:
            callable_()
        self.assertEqual(ctx.exception.code, expected)
        return ctx.exception

    def _fixed_clock(self):
        from datetime import datetime, timezone
        return datetime(2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc)

    def _decide(self, store, rq_id, seq=1, action="accept",
                claim="operator", reason="sufficient evidence"):
        return store.decide(rq_id, seq, action, claim, reason)


_DELEGATE = object()


class RecordingIo:
    """Test-local 13-op shim wrapper with deterministic fault hooks."""

    def __init__(self, *, fail=None, read_hook=None, path_limit=None):
        from human_review.review_store import _Io
        self.base = _Io()
        self.fail = fail
        self.read_hook = read_hook
        self.path_limit_hook = path_limit
        self.calls = []
        self.counts = {}

    def _before(self, name, *args):
        self.calls.append((name, args[0] if args else None))
        count = self.counts.get(name, 0) + 1
        self.counts[name] = count
        if self.fail is not None:
            exc = self.fail(name, args, count)
            if exc is not None:
                raise exc
        return count

    def lstat(self, path):
        self._before("lstat", path)
        return self.base.lstat(path)

    def scandir_names(self, path):
        self._before("scandir_names", path)
        return self.base.scandir_names(path)

    def read_bytes(self, path):
        count = self._before("read_bytes", path)
        if self.read_hook is not None:
            value = self.read_hook(path, count)
            if value is not _DELEGATE:
                return value
        return self.base.read_bytes(path)

    def open_exclusive(self, path):
        self._before("open_exclusive", path)
        return self.base.open_exclusive(path)

    def write_all(self, fd, data):
        self._before("write_all", fd)
        return self.base.write_all(fd, data)

    def close(self, fd):
        self._before("close", fd)
        return self.base.close(fd)

    def fsync_file(self, fd):
        self._before("fsync_file", fd)
        return self.base.fsync_file(fd)

    def fsync_dir(self, path):
        self._before("fsync_dir", path)
        return self.base.fsync_dir(path)

    def mkdir(self, path):
        self._before("mkdir", path)
        return self.base.mkdir(path)

    def link(self, source, target):
        self._before("link", source, target)
        return self.base.link(source, target)

    def unlink(self, path):
        self._before("unlink", path)
        return self.base.unlink(path)

    def resolve_path(self, path):
        self._before("resolve_path", path)
        return self.base.resolve_path(path)

    def path_limit(self, path):
        self._before("path_limit", path)
        if self.path_limit_hook is not None:
            return self.path_limit_hook(path)
        return self.base.path_limit(path)


class BarrierLinkIo:
    """Spawn-safe real-I/O wrapper that synchronizes the kernel link race."""

    def __init__(self, barrier):
        from human_review.review_store import _Io
        self.base = _Io()
        self.barrier = barrier
        self.unlinked = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def link(self, source, target):
        self.barrier.wait(timeout=10)
        return self.base.link(source, target)

    def unlink(self, path):
        import os
        self.unlinked.append(os.path.basename(path))
        return self.base.unlink(path)


def _hr2_spawn_worker(state, rq_id, action, nonce, barrier, result_queue):
    """Top-level spawn worker; no module-global mocks."""
    from datetime import datetime, timezone
    from threading import BrokenBarrierError
    from human_review.review_store import ReviewError, ReviewStore
    from src.learning_loop.queue_request import build_review_queue_request

    io = BarrierLinkIo(barrier)
    store = ReviewStore(
        state,
        forbidden_roots=(),
        rq_builder=build_review_queue_request,
        io=io,
        nonce_factory=lambda: nonce,
        clock_factory=lambda: datetime(
            2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc),
    )
    try:
        result = store.decide(
            rq_id, 1, action, "operator", "sufficient evidence")
        outcome = result["status"]
    except ReviewError as exc:
        outcome = "error:" + exc.code
    except BrokenBarrierError:
        outcome = "barrier-broken"
    except BaseException as exc:  # pragma: no cover - diagnostic payload
        outcome = "unexpected:" + type(exc).__name__ + ":" + str(exc)
    result_queue.put({
        "nonce": nonce, "outcome": outcome, "unlinked": io.unlinked})


class HR2PathAndG8Tests(HR2Fixture):
    """H2.1 RED — path Gate and frozen-chain jurisdiction seam."""

    def test_public_store_error_and_thirteen_op_io_surface(self):
        from human_review.review_store import ReviewError, ReviewStore, _Io
        self.assertTrue(issubclass(ReviewError, Exception))
        self.assertTrue(callable(ReviewStore))
        self.assertEqual(
            {
                "lstat", "scandir_names", "read_bytes", "open_exclusive",
                "write_all", "close", "fsync_file", "fsync_dir", "mkdir",
                "link", "unlink", "resolve_path", "path_limit",
            },
            {name for name in vars(_Io) if not name.startswith("_")},
        )

    def test_invalid_state_dir_variants_fail_closed_without_creation(self):
        import os
        state = self._state_dir()
        missing = os.path.join(state, "missing")
        self._assert_code(
            "invalid_state_dir",
            lambda: self._store(missing).decide(
                RQ_ID, 1, "accept", "claim", "reason"),
        )
        self.assertFalse(os.path.exists(missing))

        regular = os.path.join(state, "file")
        with open(regular, "wb") as handle:
            handle.write(b"x")
        self._assert_code(
            "invalid_state_dir",
            lambda: self._store(regular).decide(
                RQ_ID, 1, "accept", "claim", "reason"),
        )

        real = os.path.join(state, "real")
        os.mkdir(real)
        alias = os.path.join(state, "alias")
        os.symlink(real, alias)
        self._assert_code(
            "invalid_state_dir",
            lambda: self._store(alias).decide(
                RQ_ID, 1, "accept", "claim", "reason"),
        )

    def test_forbidden_roots_reject_both_containment_directions(self):
        import os
        parent = self._state_dir()
        state = os.path.join(parent, "state")
        os.mkdir(state)
        self._assert_code(
            "invalid_state_dir",
            lambda: self._store(state, forbidden_roots=(parent,)).decide(
                RQ_ID, 1, "accept", "claim", "reason"),
        )

        child = os.path.join(state, "child")
        os.mkdir(child)
        self._assert_code(
            "invalid_state_dir",
            lambda: self._store(state, forbidden_roots=(child,)).decide(
                RQ_ID, 1, "accept", "claim", "reason"),
        )

    def test_path_priority_vectors_are_mutually_classified(self):
        import os
        import shutil
        from pathlib import Path

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        evaluations = Path(state) / "evaluations"
        shutil.rmtree(evaluations)
        evaluations.write_bytes(b"not a directory")
        self._assert_code(
            "unsafe_path", lambda: self._decide(self._store(state), rq_id))

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        queue = Path(state) / "review_queue"
        shutil.rmtree(queue)
        queue.write_bytes(b"not a directory")
        self._assert_code(
            "malformed_review_queue_namespace",
            lambda: self._decide(self._store(state), rq_id),
        )

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        (Path(state) / "decisions").write_bytes(b"not a directory")
        self._assert_code(
            "malformed_decisions_namespace",
            lambda: self._decide(self._store(state), rq_id),
        )

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        item = Path(state) / "review_queue" / (rq_id + ".json")
        item.unlink()
        os.mkdir(item)
        self._assert_code(
            "malformed_directory_entry",
            lambda: self._decide(self._store(state), rq_id),
        )

    def test_valid_frozen_chain_returns_request_with_zero_mutation(self):
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        before = self._tree_bytes(state)
        actual = validate_frozen_review_chain(
            state, rq["review_queue_item_id"], rq_builder=self._builder())
        self.assertEqual(actual, rq)
        self.assertEqual(self._tree_bytes(state), before)

    def test_noncanonical_local_request_is_malformed_request(self):
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        path = Path(state) / "review_queue" / (
            rq["review_queue_item_id"] + ".json")
        path.write_bytes(path.read_bytes() + b"\n")
        self._assert_code(
            "malformed_request",
            lambda: validate_frozen_review_chain(
                state, rq["review_queue_item_id"], rq_builder=self._builder()),
        )

    def test_p2_step_0_and_confirmed_read_failure_are_disjoint(self):
        import shutil
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        shutil.rmtree(Path(state) / "review_queue")
        self._assert_code(
            "request_not_found",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder()),
        )

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]

        def fail_request_read(path, _count):
            if path.endswith(rq_id + ".json"):
                raise OSError("confirmed request became unreadable")
            return _DELEGATE

        self._assert_code(
            "path_read_failed",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder(),
                io=RecordingIo(read_hook=fail_request_read)),
        )

    def test_p2_local_q_five_failure_classes_are_malformed_request(self):
        import json
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        def extra_key(value):
            value["extra"] = True

        def wrong_embedded_id(value):
            value["review_queue_item_id"] = "rq_" + "b" * 64

        cases = (
            ("canonical", None),
            ("closed_schema", extra_key),
            ("embedded_identity", wrong_embedded_id),
            ("schema_version", lambda value: value.update(schema_version=2)),
            ("pending", lambda value: value.update(status="accepted")),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]
                path = Path(state) / "review_queue" / (rq_id + ".json")
                if mutate is None:
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    value = json.loads(path.read_bytes())
                    mutate(value)
                    path.write_bytes(canonical_bytes(value))
                self._assert_code(
                    "malformed_request",
                    lambda state=state, rq_id=rq_id:
                        validate_frozen_review_chain(
                            state, rq_id, rq_builder=self._builder()),
                )

    def test_p2_nested_local_q_schema_precedes_missing_locator(self):
        import json
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        cases = (
            ("experiment", lambda value: value.update(experiment={})),
            ("evidence", lambda value: value.update(evidence_summary={})),
            ("missing_information", lambda value: value.update(
                missing_information=[{"claim_id": "M-1"}])),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]
                path = Path(state) / "review_queue" / (rq_id + ".json")
                value = json.loads(path.read_bytes())
                mutate(value)
                value["source_evaluation_id"] = "eval_" + "b" * 64
                path.write_bytes(canonical_bytes(value))
                self._assert_code(
                    "malformed_request",
                    lambda state=state, rq_id=rq_id:
                        validate_frozen_review_chain(
                            state, rq_id, rq_builder=self._builder()),
                )

    def test_pathological_json_depth_is_still_fail_closed(self):
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        path = Path(state) / "review_queue" / (
            rq["review_queue_item_id"] + ".json")
        path.write_bytes(b"[" * 2000 + b"]" * 2000)
        self._assert_code(
            "malformed_request",
            lambda: validate_frozen_review_chain(
                state, rq["review_queue_item_id"], rq_builder=self._builder()),
        )

    def test_missing_upstream_chain_is_invalid_review_chain(self):
        import shutil
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        shutil.rmtree(Path(state) / "evaluations")
        self._assert_code(
            "invalid_review_chain",
            lambda: validate_frozen_review_chain(
                state, rq["review_queue_item_id"], rq_builder=self._builder()),
        )

    def test_p2_locator_and_digest_directory_failures_are_invalid_chain(self):
        import json
        import shutil
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        rq_path = Path(state) / "review_queue" / (rq_id + ".json")
        value = json.loads(rq_path.read_bytes())
        value["source_evaluation_id"] = "eval_" + "b" * 64
        rq_path.write_bytes(canonical_bytes(value))
        self._assert_code(
            "invalid_review_chain",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder()),
        )

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        digest = next((Path(state) / "evaluations").glob("eval_*/snap_*"))
        shutil.rmtree(digest)
        self._assert_code(
            "invalid_review_chain",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder()),
        )

    def test_p2_each_chain_artifact_read_and_rebuild_failure_is_classified(self):
        import json
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        names = (
            "enriched_utility_evaluation.json",
            "reflection.json",
            "eligibility.json",
        )
        for name in names:
            with self.subTest(name=name, failure="read"):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]

                def fail_artifact_read(path, _count, name=name):
                    if path.endswith(name):
                        raise OSError("artifact read failed")
                    return _DELEGATE

                self._assert_code(
                    "path_read_failed",
                    lambda state=state, rq_id=rq_id, hook=fail_artifact_read:
                        validate_frozen_review_chain(
                            state, rq_id, rq_builder=self._builder(),
                            io=RecordingIo(read_hook=hook)),
                )

            with self.subTest(name=name, failure="rebuild"):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]
                artifact = next(Path(state).glob(
                    "evaluations/eval_*/snap_*/" + name))
                value = json.loads(artifact.read_bytes())
                value["unexpected"] = True
                artifact.write_bytes(canonical_bytes(value))
                self._assert_code(
                    "invalid_review_chain",
                    lambda state=state, rq_id=rq_id:
                        validate_frozen_review_chain(
                            state, rq_id, rq_builder=self._builder()),
                )

    def test_p2_digest_binding_and_anchor_equation_fail_closed(self):
        import json
        import shutil
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        old_digest = next((Path(state) / "evaluations").glob("eval_*/snap_*"))
        new_digest_name = "snap_" + "b" * 64
        shutil.copytree(old_digest, old_digest.parent / new_digest_name)
        rq_path = Path(state) / "review_queue" / (rq_id + ".json")
        value = json.loads(rq_path.read_bytes())
        value["source_snapshot_digest"] = new_digest_name
        rq_path.write_bytes(canonical_bytes(value))
        self._assert_code(
            "invalid_review_chain",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder()),
        )

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        eligibility = next(Path(state).glob(
            "evaluations/eval_*/snap_*/eligibility.json"))
        value = json.loads(eligibility.read_bytes())
        value["source_reflection_id"] = "rf_" + "b" * 64
        eligibility.write_bytes(canonical_bytes(value))
        self._assert_code(
            "invalid_review_chain",
            lambda: validate_frozen_review_chain(
                state, rq_id, rq_builder=self._builder()),
        )

    def test_rebuilt_request_mismatch_is_malformed_request(self):
        from pathlib import Path
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        changed = dict(rq, summary=rq["summary"] + " altered")
        path = Path(state) / "review_queue" / (
            rq["review_queue_item_id"] + ".json")
        path.write_bytes(canonical_bytes(changed))
        self._assert_code(
            "malformed_request",
            lambda: validate_frozen_review_chain(
                state, rq["review_queue_item_id"], rq_builder=self._builder()),
        )


class HR2DecisionFlowTests(HR2Fixture):
    """H2.2/H2.3 RED — state, idempotency, conflict, and publish."""

    def test_empty_slot_publishes_canonical_final_and_cleans_temp(self):
        from pathlib import Path
        state, rq = self._published_chain()
        store = self._store(
            state, nonce_factory=lambda: "1" * 32,
            clock_factory=self._fixed_clock,
        )
        result = self._decide(store, rq["review_queue_item_id"])
        self.assertEqual(result["status"], "published")
        self.assertEqual(
            result["event"]["recorded_at"], "2026-07-15T04:00:00Z")
        final = Path(state) / "decisions" / (
            rq["review_queue_item_id"] + ".decision_1.json")
        self.assertEqual(final.read_bytes(), canonical_bytes(result["event"]))
        self.assertEqual(list(final.parent.glob(".tmp.*")), [])

    def test_existing_decision_without_request_is_orphan_corruption(self):
        import shutil
        from pathlib import Path

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        store = self._store(
            state, nonce_factory=lambda: "0" * 32,
            clock_factory=self._fixed_clock)
        self._decide(store, rq_id)
        shutil.rmtree(Path(state) / "review_queue")
        err = self._assert_code(
            "orphan_decision", lambda: self._decide(store, rq_id))
        self.assertEqual(err.category, "corruption")
        self.assertEqual(err.rq_id, rq_id)
        self.assertIsNone(err.decision_seq)

    def test_same_command_is_idempotent_returns_original_without_clock(self):
        state, rq = self._published_chain()
        first = self._decide(
            self._store(state, clock_factory=self._fixed_clock),
            rq["review_queue_item_id"],
        )
        calls = []

        def bad_clock():
            calls.append(True)
            raise RuntimeError("must not run")

        second = self._decide(
            self._store(state, clock_factory=bad_clock),
            rq["review_queue_item_id"],
        )
        self.assertEqual(second, {"status": "idempotent", "event": first["event"]})
        self.assertEqual(calls, [])

    def test_different_projection_conflicts_without_overwrite_or_clock(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        self._decide(self._store(state, clock_factory=self._fixed_clock), rq_id)
        final = Path(state) / "decisions" / (rq_id + ".decision_1.json")
        before = final.read_bytes()
        calls = []
        self._assert_code(
            "decision_slot_conflict",
            lambda: self._decide(
                self._store(
                    state,
                    clock_factory=lambda: calls.append(True)),
                rq_id,
                reason="different",
            ),
        )
        self.assertEqual(calls, [])
        self.assertEqual(final.read_bytes(), before)

    def test_seq2_requires_deferred_state_and_cannot_defer(self):
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        store = self._store(state, clock_factory=self._fixed_clock)
        self._assert_code(
            "invalid_state_transition",
            lambda: self._decide(store, rq_id, seq=2),
        )
        first = self._decide(store, rq_id, action="defer")
        self.assertEqual(first["status"], "published")
        self._assert_code(
            "invalid_state_transition",
            lambda: self._decide(store, rq_id, seq=2, action="defer"),
        )
        second = self._decide(store, rq_id, seq=2, action="revise")
        self.assertEqual(second["status"], "published")

    def test_invalid_state_transition_never_calls_clock(self):
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        calls = []
        self._assert_code(
            "invalid_state_transition",
            lambda: self._decide(
                self._store(
                    state, clock_factory=lambda: calls.append(True)),
                rq_id, seq=2),
        )
        self.assertEqual(calls, [])

    def test_terminal_seq1_rejects_new_seq2_but_same_seq1_is_idempotent(self):
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        store = self._store(state, clock_factory=self._fixed_clock)
        first = self._decide(store, rq_id, action="reject")
        retry = self._decide(store, rq_id, action="reject")
        self.assertEqual(retry, {"status": "idempotent", "event": first["event"]})
        self._assert_code(
            "invalid_state_transition",
            lambda: self._decide(store, rq_id, seq=2, action="accept"),
        )

    def test_malformed_existing_decision_fails_closed_without_overwrite(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        decisions = Path(state) / "decisions"
        decisions.mkdir()
        final = decisions / (rq_id + ".decision_1.json")
        final.write_bytes(b"{}")
        self._assert_code(
            "malformed_decision",
            lambda: self._decide(
                self._store(state, clock_factory=self._fixed_clock), rq_id),
        )
        self.assertEqual(final.read_bytes(), b"{}")

    def test_illegal_event_combination_fails_before_idempotent_comparison(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        decisions = Path(state) / "decisions"
        decisions.mkdir()
        seq1 = build_decision(
            rq_id, 1, "accept", "operator", "sufficient evidence",
            RECORDED_AT)
        seq2 = build_decision(
            rq_id, 2, "accept", "operator", "sufficient evidence",
            RECORDED_AT)
        (decisions / (rq_id + ".decision_1.json")).write_bytes(
            canonical_bytes(seq1))
        (decisions / (rq_id + ".decision_2.json")).write_bytes(
            canonical_bytes(seq2))
        self._assert_code(
            "malformed_decision",
            lambda: self._decide(
                self._store(state, clock_factory=self._fixed_clock), rq_id),
        )

    def test_clock_unavailable_only_for_publishable_empty_slot(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]

        def bad_clock():
            raise RuntimeError("clock offline")

        self._assert_code(
            "clock_unavailable",
            lambda: self._decide(
                self._store(state, clock_factory=bad_clock), rq_id),
        )
        self.assertFalse((Path(state) / "decisions").exists())

    def test_request_not_found_and_bad_input_codes_are_precise(self):
        state = self._state_dir()
        store = self._store(state, clock_factory=self._fixed_clock)
        err = self._assert_code(
            "request_not_found",
            lambda: self._decide(store, RQ_ID),
        )
        self.assertEqual(err.rq_id, RQ_ID)
        self.assertEqual(err.decision_seq, 1)
        err = self._assert_code(
            "invalid_review_queue_item_id",
            lambda: self._decide(store, "bad"),
        )
        self.assertIsNone(err.rq_id)
        self.assertIsNone(err.decision_seq)
        err = self._assert_code(
            "invalid_decision_seq",
            lambda: self._decide(store, RQ_ID, seq=True),
        )
        self.assertEqual(err.rq_id, RQ_ID)
        self.assertIsNone(err.decision_seq)
        self._assert_code(
            "invalid_action",
            lambda: self._decide(store, RQ_ID, action="approve"),
        )

    def test_g8_errors_include_parsed_slot_context(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        path = Path(state) / "review_queue" / (rq_id + ".json")
        path.write_bytes(path.read_bytes() + b"\n")
        err = self._assert_code(
            "malformed_request",
            lambda: self._decide(
                self._store(state, clock_factory=self._fixed_clock), rq_id),
        )
        self.assertEqual(err.rq_id, rq_id)
        self.assertEqual(err.decision_seq, 1)


class HR2DurabilityAndFaultTests(HR2Fixture):
    """H2.3/H2.4 — barrier, temp ownership, and fault classification."""

    def test_publish_fsync_order_covers_observation_bootstrap_handoff_commit(self):
        import os
        state, rq = self._published_chain()
        io = RecordingIo()
        self._decide(
            self._store(
                state, io=io, nonce_factory=lambda: "2" * 32,
                clock_factory=self._fixed_clock),
            rq["review_queue_item_id"],
        )
        root = os.path.realpath(state)
        queue = os.path.join(root, "review_queue")
        decisions = os.path.join(root, "decisions")
        fsyncs = [value for name, value in io.calls if name == "fsync_dir"]
        self.assertEqual(fsyncs, [queue, root, queue, decisions])

    def test_barrier_failure_precedes_invalid_state_transition(self):
        import os
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        self._decide(
            self._store(state, clock_factory=self._fixed_clock), rq_id)
        decisions = os.path.join(os.path.realpath(state), "decisions")

        def fail(name, args, _count):
            if name == "fsync_dir" and args[0] == decisions:
                return OSError("barrier failed")
            return None

        self._assert_code(
            "precommit_directory_fsync_failed",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(fail=fail),
                    clock_factory=self._fixed_clock),
                rq_id, seq=2),
        )

    def test_all_three_precommit_fsync_points_share_one_code(self):
        import os
        from pathlib import Path
        for stage in ("observation", "bootstrap", "handoff"):
            with self.subTest(stage=stage):
                state, rq = self._published_chain()
                root = os.path.realpath(state)
                queue = os.path.join(root, "review_queue")
                seen_queue = []

                def fail(name, args, _count, stage=stage):
                    if name != "fsync_dir":
                        return None
                    path = args[0]
                    if path == queue:
                        seen_queue.append(path)
                        if stage == "observation" and len(seen_queue) == 1:
                            return OSError(stage)
                        if stage == "handoff" and len(seen_queue) == 2:
                            return OSError(stage)
                    if stage == "bootstrap" and path == root:
                        return OSError(stage)
                    return None

                self._assert_code(
                    "precommit_directory_fsync_failed",
                    lambda: self._decide(
                        self._store(
                            state, io=RecordingIo(fail=fail),
                            nonce_factory=lambda: "e" * 32,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )
                self.assertEqual(
                    list((Path(state) / "decisions").glob("*.json")), [])

    def test_slot_reader_reads_seq2_before_seq1(self):
        import os
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        store = self._store(state, clock_factory=self._fixed_clock)
        self._decide(store, rq_id, action="defer")
        self._decide(store, rq_id, seq=2, action="accept")
        io = RecordingIo()
        self._decide(
            self._store(state, io=io, clock_factory=self._fixed_clock),
            rq_id, seq=2, action="accept")
        reads = [
            os.path.basename(value) for name, value in io.calls
            if name == "read_bytes" and "decision_" in os.path.basename(value)
        ]
        self.assertEqual(reads[:2], [
            rq_id + ".decision_2.json", rq_id + ".decision_1.json"])

    def test_decisions_namespace_file_and_symlink_are_distinct_corruption(self):
        import os
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                state, rq = self._published_chain()
                decisions = os.path.join(os.path.realpath(state), "decisions")
                if kind == "file":
                    with open(decisions, "wb") as handle:
                        handle.write(b"x")
                else:
                    target = self._state_dir()
                    os.symlink(target, decisions)
                err = self._assert_code(
                    "malformed_decisions_namespace",
                    lambda: self._decide(
                        self._store(state, clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )
                self.assertIsNone(err.rq_id)
                self.assertIsNone(err.decision_seq)

    def test_unknown_entry_fails_but_any_tmp_prefix_is_ignored_and_never_deleted(self):
        from pathlib import Path
        state, rq = self._published_chain()
        decisions = Path(state) / "decisions"
        decisions.mkdir()
        (decisions / "unknown.json").write_bytes(b"x")
        self._assert_code(
            "malformed_directory_entry",
            lambda: self._decide(
                self._store(state, clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )

        (decisions / "unknown.json").unlink()
        foreign = decisions / ".tmp.foreign"
        foreign.mkdir()
        self._decide(
            self._store(state, clock_factory=self._fixed_clock),
            rq["review_queue_item_id"])
        self.assertTrue(foreign.is_dir())

    def test_temp_full_path_limit_is_checked_after_final_path(self):
        import os
        from pathlib import Path
        state, rq = self._published_chain()

        def limit(path):
            if os.path.basename(path).startswith(".tmp."):
                return len(os.fsencode(path)) - 1
            return 100000

        self._assert_code(
            "path_too_long",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(path_limit=limit),
                    nonce_factory=lambda: "3" * 32,
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )
        self.assertEqual(list((Path(state) / "decisions").glob("*.json")), [])

    def test_final_full_path_limit_fails_before_decisions_bootstrap(self):
        import os
        from pathlib import Path

        state, rq = self._published_chain()

        def limit(path):
            if os.path.basename(path).endswith(".decision_1.json"):
                return len(os.fsencode(path)) - 1
            return 100000

        self._assert_code(
            "path_too_long",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(path_limit=limit),
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )
        self.assertFalse((Path(state) / "decisions").exists())

    def test_nonce_invalid_vectors_have_zero_io_after_factory_call(self):
        invalid = [
            None, "", "A" * 32, "g" * 32, "/" * 32, "\\" * 32,
            ".." + "a" * 30, "a" * 31, "a" * 33,
        ]
        for value in invalid + [RuntimeError("nonce offline")]:
            with self.subTest(value=repr(value)):
                state, rq = self._published_chain()
                io = RecordingIo()
                marker = []

                def factory(value=value):
                    marker.append(len(io.calls))
                    if isinstance(value, Exception):
                        raise value
                    return value

                self._assert_code(
                    "temp_write_failed",
                    lambda: self._decide(
                        self._store(
                            state, io=io, nonce_factory=factory,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )
                self.assertEqual(len(io.calls), marker[0])

    def test_temp_write_failure_matrix_closes_fd_and_removes_own_temp(self):
        from pathlib import Path
        for operation in ("open_exclusive", "write_all", "fsync_file", "close"):
            with self.subTest(operation=operation):
                state, rq = self._published_chain()

                def fail(name, _args, _count, operation=operation):
                    return OSError(operation) if name == operation else None

                io = RecordingIo(fail=fail)
                self._assert_code(
                    "temp_write_failed",
                    lambda: self._decide(
                        self._store(
                            state, io=io, nonce_factory=lambda: "4" * 32,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )
                decisions = Path(state) / "decisions"
                self.assertEqual(list(decisions.glob(".tmp.*")), [])
                if operation != "open_exclusive":
                    self.assertEqual(io.counts.get("open_exclusive"), 1)
                    self.assertEqual(io.counts.get("close"), 1)

    def test_temp_write_failures_pair_exact_opened_and_closed_fds(self):
        from human_review.review_store import _Io

        for operation in (
                "open_exclusive", "write_all", "fsync_file", "close"):
            with self.subTest(operation=operation):
                state, rq = self._published_chain()

                class FdAccountingIo:
                    def __init__(self):
                        self.base = _Io()
                        self.opened = set()
                        self.closed = set()

                    def __getattr__(self, name):
                        return getattr(self.base, name)

                    def open_exclusive(self, path):
                        if operation == "open_exclusive":
                            raise OSError("open failed")
                        fd = self.base.open_exclusive(path)
                        self.opened.add(fd)
                        return fd

                    def write_all(self, fd, data):
                        if operation == "write_all":
                            raise OSError("write failed")
                        return self.base.write_all(fd, data)

                    def fsync_file(self, fd):
                        if operation == "fsync_file":
                            raise OSError("fsync failed")
                        return self.base.fsync_file(fd)

                    def close(self, fd):
                        self.base.close(fd)
                        self.closed.add(fd)
                        if operation == "close":
                            raise OSError("close result unavailable")

                io = FdAccountingIo()
                self._assert_code(
                    "temp_write_failed",
                    lambda: self._decide(
                        self._store(
                            state, io=io,
                            nonce_factory=lambda: "4" * 32,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )
                self.assertEqual(io.opened, io.closed)

    def test_temp_read_failure_codes_are_disjoint(self):
        import os
        cases = (
            ("temp_byte_mismatch", lambda _path: b"wrong"),
            ("temp_missing_before_link", lambda path: (_ for _ in ()).throw(
                FileNotFoundError(path))),
            ("temp_read_failed", lambda _path: (_ for _ in ()).throw(
                OSError("read failed"))),
        )
        for expected, behavior in cases:
            with self.subTest(expected=expected):
                state, rq = self._published_chain()

                def read_hook(path, _count, behavior=behavior):
                    if os.path.basename(path).startswith(".tmp."):
                        return behavior(path)
                    return _DELEGATE

                self._assert_code(
                    expected,
                    lambda: self._decide(
                        self._store(
                            state, io=RecordingIo(read_hook=read_hook),
                            nonce_factory=lambda: "5" * 32,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )

    def test_link_failure_keeps_original_code_when_cleanup_also_fails(self):
        def fail(name, _args, _count):
            if name == "link":
                return PermissionError("link denied")
            if name == "unlink":
                return OSError("cleanup denied")
            return None

        state, rq = self._published_chain()
        self._assert_code(
            "link_failed",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(fail=fail),
                    nonce_factory=lambda: "6" * 32,
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )

    def test_all_temp_main_errors_survive_cleanup_failure(self):
        import os

        cases = (
            ("write_all", "temp_write_failed", None),
            ("fsync_file", "temp_write_failed", None),
            ("close", "temp_write_failed", None),
            (None, "temp_missing_before_link", "missing"),
            (None, "temp_read_failed", "read"),
            (None, "temp_byte_mismatch", "mismatch"),
            ("link", "link_failed", None),
        )
        for operation, expected, read_behavior in cases:
            with self.subTest(
                    operation=operation, read_behavior=read_behavior):
                state, rq = self._published_chain()

                def fail(name, _args, _count, operation=operation):
                    if name == "unlink":
                        return OSError("cleanup failed")
                    if name == operation:
                        return OSError("main operation failed")
                    return None

                def read_hook(path, _count, behavior=read_behavior):
                    if not os.path.basename(path).startswith(".tmp."):
                        return _DELEGATE
                    if behavior == "missing":
                        raise FileNotFoundError(path)
                    if behavior == "read":
                        raise OSError("temp read failed")
                    if behavior == "mismatch":
                        return b"wrong"
                    return _DELEGATE

                self._assert_code(
                    expected,
                    lambda: self._decide(
                        self._store(
                            state,
                            io=RecordingIo(
                                fail=fail, read_hook=read_hook),
                            nonce_factory=lambda: "6" * 32,
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )

    def test_post_link_failures_never_roll_back_final(self):
        import os
        from pathlib import Path
        cases = (
            "decision_directory_fsync_failed",
            "final_readback_failed",
            "decision_final_byte_mismatch",
        )
        for expected in cases:
            with self.subTest(expected=expected):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]
                decisions = os.path.join(os.path.realpath(state), "decisions")

                def fail(name, args, _count, expected=expected):
                    if expected == "decision_directory_fsync_failed" \
                            and name == "fsync_dir" and args[0] == decisions:
                        return OSError("post-link fsync")
                    return None

                def read_hook(path, _count, expected=expected):
                    if path.endswith(".decision_1.json"):
                        if expected == "final_readback_failed":
                            raise OSError("final read")
                        if expected == "decision_final_byte_mismatch":
                            return b"wrong"
                    return _DELEGATE

                self._assert_code(
                    expected,
                    lambda: self._decide(
                        self._store(
                            state,
                            io=RecordingIo(fail=fail, read_hook=read_hook),
                            nonce_factory=lambda: "7" * 32,
                            clock_factory=self._fixed_clock),
                        rq_id),
                )
                final = Path(decisions) / (rq_id + ".decision_1.json")
                self.assertTrue(final.exists())

    def test_success_ignores_own_temp_cleanup_failure_and_leaves_residue(self):
        from pathlib import Path

        def fail(name, args, _count):
            if name == "unlink" and Path(args[0]).name.startswith(".tmp."):
                return OSError("cleanup denied")
            return None

        state, rq = self._published_chain()
        result = self._decide(
            self._store(
                state, io=RecordingIo(fail=fail),
                nonce_factory=lambda: "8" * 32,
                clock_factory=self._fixed_clock),
            rq["review_queue_item_id"],
        )
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(list((Path(state) / "decisions").glob(".tmp.*"))), 1)

    def test_path_limit_query_failures_map_to_path_read_failed(self):
        values = (None, True, "4096", 0, -1)
        for value in values:
            with self.subTest(value=value):
                state, rq = self._published_chain()
                self._assert_code(
                    "path_read_failed",
                    lambda: self._decide(
                        self._store(
                            state,
                            io=RecordingIo(path_limit=lambda _path, value=value: value),
                            clock_factory=self._fixed_clock),
                        rq["review_queue_item_id"]),
                )

        state, rq = self._published_chain()

        def fail_path_limit(name, _args, _count):
            return OSError("pathconf failed") if name == "path_limit" else None

        self._assert_code(
            "path_read_failed",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(fail=fail_path_limit),
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )

    def test_clock_invalid_return_vectors_are_clock_unavailable(self):
        from datetime import datetime, timedelta, timezone
        values = (
            None,
            "2026-07-15T04:00:00Z",
            datetime(2026, 7, 15, 4, 0, 0),
            datetime(2026, 7, 15, 4, 0, 0,
                     tzinfo=timezone(timedelta(hours=1))),
        )
        for value in values:
            with self.subTest(value=repr(value)):
                state, rq = self._published_chain()
                self._assert_code(
                    "clock_unavailable",
                    lambda: self._decide(
                        self._store(state, clock_factory=lambda value=value: value),
                        rq["review_queue_item_id"]),
                )

    def test_bootstrap_mkdir_failure_has_no_rq_or_seq_context(self):
        state, rq = self._published_chain()

        def fail(name, _args, _count):
            return PermissionError("mkdir denied") if name == "mkdir" else None

        err = self._assert_code(
            "mkdir_failed",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(fail=fail),
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )
        self.assertIsNone(err.rq_id)
        self.assertIsNone(err.decision_seq)

    def test_bootstrap_eexist_then_lstat_failure_is_mkdir_failed(self):
        import os

        state, rq = self._published_chain()
        decisions = os.path.join(os.path.realpath(state), "decisions")
        mkdir_attempted = []

        def fail(name, args, _count):
            if name == "mkdir":
                mkdir_attempted.append(True)
                return FileExistsError("simulated concurrent creator")
            if name == "lstat" and args[0] == decisions and mkdir_attempted:
                return OSError("post-EEXIST lstat failed")
            return None

        err = self._assert_code(
            "mkdir_failed",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(fail=fail),
                    clock_factory=self._fixed_clock),
                rq["review_queue_item_id"]),
        )
        self.assertIsNone(err.rq_id)
        self.assertIsNone(err.decision_seq)

    def test_post_publish_final_missing_is_outcome_uncertain(self):
        import os
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]

        def read_hook(path, _count):
            if path.endswith(".decision_1.json"):
                os.unlink(path)
                raise FileNotFoundError(path)
            return _DELEGATE

        self._assert_code(
            "decision_final_missing",
            lambda: self._decide(
                self._store(
                    state, io=RecordingIo(read_hook=read_hook),
                    nonce_factory=lambda: "9" * 32,
                    clock_factory=self._fixed_clock),
                rq_id),
        )
        self.assertFalse(
            (Path(state) / "decisions" / (rq_id + ".decision_1.json")).exists())


class HR2ConcurrencyTests(HR2Fixture):
    """H2.4 — deterministic Event interleavings and real spawn/os.link."""

    def test_event_temp_interleavings_cover_same_and_conflicting_commands(self):
        import os
        import threading
        from human_review.review_store import _Io

        pause_points = (
            "open_exclusive", "write_all", "fsync_file", "close",
            "temp_read", "link",
        )
        for pause_at in pause_points:
            for action_b in ("accept", "reject"):
                with self.subTest(pause_at=pause_at, action_b=action_b):
                    state, rq = self._published_chain()
                    rq_id = rq["review_queue_item_id"]
                    paused = threading.Event()
                    resume = threading.Event()

                    class TrackingIo(_Io):
                        def __init__(self, nonce):
                            self.nonce = nonce
                            self.unlinked = []

                        def unlink(self, path):
                            self.unlinked.append(os.path.basename(path))
                            return super().unlink(path)

                    class PauseIo(TrackingIo):
                        def _pause(self):
                            paused.set()
                            if not resume.wait(timeout=10):
                                raise TimeoutError("resume timeout")

                        def open_exclusive(self, path):
                            result = super().open_exclusive(path)
                            if pause_at == "open_exclusive":
                                self._pause()
                            return result

                        def write_all(self, fd, data):
                            result = super().write_all(fd, data)
                            if pause_at == "write_all":
                                self._pause()
                            return result

                        def fsync_file(self, fd):
                            result = super().fsync_file(fd)
                            if pause_at == "fsync_file":
                                self._pause()
                            return result

                        def close(self, fd):
                            result = super().close(fd)
                            if pause_at == "close":
                                self._pause()
                            return result

                        def read_bytes(self, path):
                            result = super().read_bytes(path)
                            if pause_at == "temp_read" \
                                    and os.path.basename(path).startswith(".tmp."):
                                self._pause()
                            return result

                        def link(self, source, target):
                            result = super().link(source, target)
                            if pause_at == "link":
                                self._pause()
                            return result

                    io_a = PauseIo("a" * 32)
                    io_b = TrackingIo("b" * 32)
                    outcomes = {}

                    def writer_a():
                        try:
                            outcomes["a"] = self._decide(
                                self._store(
                                    state, io=io_a,
                                    nonce_factory=lambda: io_a.nonce,
                                    clock_factory=self._fixed_clock),
                                rq_id)["status"]
                        except BaseException as exc:  # diagnostic assertion
                            outcomes["a"] = "error:" + getattr(
                                exc, "code", type(exc).__name__)

                    thread = threading.Thread(target=writer_a)
                    thread.start()
                    self.assertTrue(paused.wait(timeout=10))
                    try:
                        try:
                            outcomes["b"] = self._decide(
                                self._store(
                                    state, io=io_b,
                                    nonce_factory=lambda: io_b.nonce,
                                    clock_factory=self._fixed_clock),
                                rq_id, action=action_b)["status"]
                        except BaseException as exc:  # diagnostic assertion
                            outcomes["b"] = "error:" + getattr(
                                exc, "code", type(exc).__name__)
                    finally:
                        resume.set()
                        thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                    expected = {"published", "idempotent"} \
                        if action_b == "accept" else {
                            "published", "error:decision_slot_conflict"}
                    self.assertEqual(set(outcomes.values()), expected)
                    self.assertTrue(io_a.unlinked)
                    for io in (io_a, io_b):
                        self.assertTrue(all(
                            name.endswith(io.nonce) for name in io.unlinked))

    def test_bootstrap_creator_and_late_writer_both_fsync_root(self):
        import os
        import threading
        from human_review.review_store import _Io

        for creator_name in ("a", "b"):
            with self.subTest(creator=creator_name):
                state, rq = self._published_chain()
                rq_id = rq["review_queue_item_id"]
                late_ready = threading.Event()
                creator_done = threading.Event()
                outcomes = {}

                class BootstrapIo(_Io):
                    def __init__(self, name):
                        self.name = name
                        self.fsynced = []

                    def mkdir(self, path):
                        if self.name == creator_name:
                            if not late_ready.wait(timeout=10):
                                raise TimeoutError("late writer did not arrive")
                            result = super().mkdir(path)
                            creator_done.set()
                            return result
                        late_ready.set()
                        if not creator_done.wait(timeout=10):
                            raise TimeoutError("creator did not finish")
                        return super().mkdir(path)

                    def fsync_dir(self, path):
                        self.fsynced.append(path)
                        return super().fsync_dir(path)

                ios = {name: BootstrapIo(name) for name in ("a", "b")}

                def writer(name):
                    try:
                        outcomes[name] = self._decide(
                            self._store(
                                state, io=ios[name],
                                nonce_factory=lambda name=name: name * 32,
                                clock_factory=self._fixed_clock),
                            rq_id)["status"]
                    except BaseException as exc:  # diagnostic assertion below
                        outcomes[name] = "error:" + getattr(
                            exc, "code", type(exc).__name__)

                threads = [
                    threading.Thread(target=writer, args=(name,))
                    for name in ("a", "b")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(
                    sorted(outcomes.values()), ["idempotent", "published"])
                for io in ios.values():
                    self.assertIn(os.path.realpath(state), io.fsynced)

    def _spawn_race(self, actions):
        import multiprocessing
        import queue
        from pathlib import Path

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        result_queue = ctx.Queue()
        nonces = ("c" * 32, "d" * 32)
        workers = [
            ctx.Process(
                target=_hr2_spawn_worker,
                args=(state, rq_id, action, nonce, barrier, result_queue),
            )
            for action, nonce in zip(actions, nonces)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)
        try:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "spawn worker hung")
                self.assertEqual(worker.exitcode, 0)
            results = [result_queue.get(timeout=5) for _ in workers]
        except queue.Empty:
            self.fail("spawn worker produced no result")

        self.assertNotIn("barrier-broken", {item["outcome"] for item in results})
        for item in results:
            self.assertTrue(item["unlinked"])
            self.assertTrue(all(name.endswith(item["nonce"])
                                for name in item["unlinked"]))
        decisions = Path(state) / "decisions"
        self.assertEqual(len(list(decisions.glob("*.json"))), 1)
        self.assertEqual(list(decisions.glob(".tmp.*")), [])
        return {item["outcome"] for item in results}

    def test_spawn_same_command_has_one_published_one_idempotent(self):
        self.assertEqual(
            self._spawn_race(("accept", "accept")),
            {"published", "idempotent"},
        )

    def test_spawn_different_command_has_one_published_one_conflict(self):
        self.assertEqual(
            self._spawn_race(("accept", "reject")),
            {"published", "error:decision_slot_conflict"},
        )

    def test_production_io_link_delegates_to_os_link(self):
        from unittest import mock
        from human_review.review_store import _Io
        with mock.patch("human_review.review_store.os.link") as linked:
            _Io().link("source", "target")
        linked.assert_called_once_with("source", "target")


class HR2ScopeAuditTests(HR2Fixture):
    """H2.5 static/dynamic audit for the frozen two-file capability seam."""

    def test_g8_standalone_uses_only_five_read_operations(self):
        from human_review.review_store import validate_frozen_review_chain
        state, rq = self._published_chain()
        io = RecordingIo()
        validate_frozen_review_chain(
            state,
            rq["review_queue_item_id"],
            rq_builder=self._builder(),
            io=io,
        )
        self.assertLessEqual(
            {name for name, _value in io.calls},
            {"lstat", "resolve_path", "path_limit", "read_bytes",
             "scandir_names"},
        )

    def test_g8_failure_has_no_fsync_mutation_or_clock(self):
        from pathlib import Path
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        path = Path(state) / "review_queue" / (rq_id + ".json")
        path.write_bytes(path.read_bytes() + b"\n")
        io = RecordingIo()
        clocks = []
        self._assert_code(
            "malformed_request",
            lambda: self._decide(
                self._store(
                    state,
                    io=io,
                    clock_factory=lambda: clocks.append(True)),
                rq_id),
        )
        self.assertEqual(clocks, [])
        self.assertTrue({
            "fsync_dir", "open_exclusive", "write_all", "mkdir", "link",
            "unlink",
        }.isdisjoint({name for name, _value in io.calls}))

    def test_human_review_and_learning_loop_have_zero_mutual_imports(self):
        import ast

        def imports(path):
            found = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            return found

        store = SOURCE_DIR / "human_review" / "review_store.py"
        self.assertTrue(all(
            not name.startswith(("learning_loop", "src.learning_loop",
                                 "memory", "src.memory"))
            for name in imports(store)
        ))
        for path in (SOURCE_DIR / "learning_loop").glob("*.py"):
            self.assertTrue(all(
                not name.startswith(("human_review", "src.human_review"))
                for name in imports(path)
            ), str(path))

    def test_filesystem_syscalls_are_confined_to_io_shim(self):
        import ast
        path = SOURCE_DIR / "human_review" / "review_store.py"
        tree = ast.parse(path.read_text())
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        forbidden_os = {
            "lstat", "listdir", "open", "write", "close", "fsync",
            "mkdir", "link", "unlink", "replace", "pathconf",
        }
        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                name = "open"
            elif isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "os" \
                    and node.func.attr in forbidden_os:
                name = "os." + node.func.attr
            if name is None:
                continue
            parent = node
            owner = None
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, ast.ClassDef):
                    owner = parent.name
                    break
            if owner != "_Io":
                violations.append((name, node.lineno, owner))
        self.assertEqual(violations, [])

    def test_hr2_raise_codes_are_all_frozen_and_replace_is_absent(self):
        import ast
        from human_review.decision import ERROR_CODES
        path = SOURCE_DIR / "human_review" / "review_store.py"
        source = path.read_text()
        tree = ast.parse(source)
        emitted = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_raise"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        self.assertLessEqual(emitted, set(ERROR_CODES))
        self.assertNotIn("os.replace", source)


if __name__ == "__main__":
    unittest.main()


class ReverseScandirIo(RecordingIo):
    """Return directory entries opposite to filesystem order."""

    def scandir_names(self, path):
        self._before("scandir_names", path)
        return list(reversed(self.base.scandir_names(path)))


class HR3Fixture(HR2Fixture):
    """Append-only H3.1 helpers for ReviewStore read interfaces."""

    def _copy_request(self, request, rq_id):
        value = dict(request)
        value["review_queue_item_id"] = rq_id
        return value

    def _write_request(self, state, request):
        from pathlib import Path
        queue = Path(state) / "review_queue"
        queue.mkdir(exist_ok=True)
        rq_id = request["review_queue_item_id"]
        (queue / (rq_id + ".json")).write_bytes(canonical_bytes(request))

    def _write_event(self, state, rq_id, seq, action):
        from pathlib import Path
        event = build_decision(
            rq_id, seq, action, "operator", "sufficient evidence",
            RECORDED_AT,
        )
        decisions = Path(state) / "decisions"
        decisions.mkdir(exist_ok=True)
        path = decisions / f"{rq_id}.decision_{seq}.json"
        path.write_bytes(canonical_bytes(event))
        return event

    def _replace_with(self, path, kind):
        import os
        import shutil
        from pathlib import Path
        path = Path(path)
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        if kind == "file":
            path.write_bytes(b"x")
        elif kind == "directory":
            path.mkdir()
        elif kind == "symlink":
            os.symlink(self._state_dir(), path)
        else:  # pragma: no cover - test helper contract
            raise AssertionError(kind)


class HR3ReadNamespaceTests(HR3Fixture):
    """H3.1 RED-11 namespace, bounded-pass, and entry-language vectors."""

    def test_three_handle_missing_namespace_matrix(self):
        from pathlib import Path

        # Q missing while an empty D namespace exists.
        state = self._state_dir()
        (Path(state) / "decisions").mkdir()
        store = self._store(state, clock_factory=self._fixed_clock)
        self._assert_code("request_not_found", lambda: store.show(RQ_ID))
        self._assert_code(
            "request_not_found", lambda: self._decide(store, RQ_ID))
        self.assertEqual(store.list(), {"items": []})

        # D missing is a legal empty event set for all three handles.
        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        store = self._store(
            state, nonce_factory=lambda: "7" * 32,
            clock_factory=self._fixed_clock)
        self.assertEqual(store.show(rq_id)["state"], "pending")
        self.assertEqual(
            store.list(), {"items": [{"rq_id": rq_id, "state": "pending"}]})
        self.assertEqual(self._decide(store, rq_id)["status"], "published")

        # Both namespaces missing preserves the same missing/empty semantics.
        state = self._state_dir()
        store = self._store(state, clock_factory=self._fixed_clock)
        self._assert_code("request_not_found", lambda: store.show(RQ_ID))
        self._assert_code(
            "request_not_found", lambda: self._decide(store, RQ_ID))
        self.assertEqual(store.list(), {"items": []})

    def test_list_missing_namespaces_skip_fsync(self):
        import os
        from pathlib import Path

        cases = ("review_queue", "decisions", "both")
        for missing in cases:
            with self.subTest(missing=missing):
                if missing == "both":
                    state = self._state_dir()
                    expected = {"items": []}
                else:
                    state, rq = self._published_chain()
                    expected = {"items": [{
                        "rq_id": rq["review_queue_item_id"],
                        "state": "pending",
                    }]}
                    if missing == "review_queue":
                        import shutil
                        shutil.rmtree(Path(state) / "review_queue")
                        (Path(state) / "decisions").mkdir()
                        expected = {"items": []}
                    else:
                        self.assertFalse((Path(state) / "decisions").exists())
                io = RecordingIo()
                self.assertEqual(self._store(state, io=io).list(), expected)
                fsyncs = {
                    os.path.basename(path)
                    for name, path in io.calls if name == "fsync_dir"
                }
                self.assertNotIn(missing, fsyncs)
                if missing == "both":
                    self.assertEqual(fsyncs, set())

    def test_list_orphan_after_exactly_two_passes(self):
        import shutil
        from pathlib import Path

        state, rq = self._published_chain()
        rq_id = rq["review_queue_item_id"]
        self._write_event(state, rq_id, 1, "accept")
        shutil.rmtree(Path(state) / "review_queue")
        io = RecordingIo()
        err = self._assert_code(
            "orphan_decision", lambda: self._store(state, io=io).list())
        self.assertEqual(err.rq_id, rq_id)
        decisions = str(Path(state).resolve() / "decisions")
        self.assertEqual(
            sum(name == "scandir_names" and path == decisions
                for name, path in io.calls),
            2,
        )

    def test_list_pass2_success_reduces_second_snapshot_and_new_q(self):
        import shutil
        from pathlib import Path

        state, request = self._published_chain()
        queue = Path(state).resolve() / "review_queue"
        saved = dict(request)
        shutil.rmtree(queue)
        original_id = request["review_queue_item_id"]
        pending_id = "rq_" + "b" * 64
        deferred_id = "rq_" + "c" * 64
        self._write_event(state, original_id, 1, "accept")
        queue_checks = []

        def install_second_snapshot(name, args, _count):
            if name == "lstat" and Path(args[0]) == queue:
                queue_checks.append(args[0])
                if len(queue_checks) == 2:
                    self._write_request(state, saved)
                    self._write_request(
                        state, self._copy_request(saved, pending_id))
                    self._write_request(
                        state, self._copy_request(saved, deferred_id))
                    self._write_event(state, deferred_id, 1, "defer")
            return None

        io = RecordingIo(fail=install_second_snapshot)
        self.assertEqual(self._store(state, io=io).list(), {"items": [
            {"rq_id": pending_id, "state": "pending"},
            {"rq_id": deferred_id, "state": "deferred"},
        ]})
        root = Path(state).resolve()
        queue_path = str(root / "review_queue")
        decisions_path = str(root / "decisions")
        namespace_calls = [
            (name, path) for name, path in io.calls
            if (name == "fsync_dir" or name == "scandir_names")
            and path in (queue_path, decisions_path)
        ]
        self.assertEqual(namespace_calls, [
            ("fsync_dir", decisions_path),
            ("scandir_names", decisions_path),
            ("fsync_dir", queue_path),
            ("fsync_dir", decisions_path),
            ("scandir_names", decisions_path),
            ("scandir_names", queue_path),
        ])

    def test_list_second_gate_disappearance_uses_missing_semantics(self):
        import shutil
        from pathlib import Path

        for vanished in ("review_queue", "decisions"):
            with self.subTest(vanished=vanished):
                state, request = self._published_chain()
                orphan_id = "rq_" + "b" * 64
                self._write_event(state, orphan_id, 1, "accept")
                target = Path(state).resolve() / vanished
                gate_count = []

                def vanish_on_second_gate(name, args, _count):
                    if name == "lstat" and Path(args[0]) == target:
                        gate_count.append(args[0])
                        if len(gate_count) == 2:
                            shutil.rmtree(target)
                    return None

                io = RecordingIo(fail=vanish_on_second_gate)
                if vanished == "review_queue":
                    self._assert_code(
                        "orphan_decision",
                        lambda: self._store(state, io=io).list())
                else:
                    self.assertEqual(self._store(state, io=io).list(), {
                        "items": [{
                            "rq_id": request["review_queue_item_id"],
                            "state": "pending",
                        }],
                    })
                target_path = str(target.resolve())
                self.assertEqual(
                    sum(name == "fsync_dir" and path == target_path
                        for name, path in io.calls),
                    1,
                )

    def test_list_second_gate_invalid_namespace_does_not_blind_fsync(self):
        from pathlib import Path

        expected = {
            "review_queue": "malformed_review_queue_namespace",
            "decisions": "malformed_decisions_namespace",
        }
        for namespace, code in expected.items():
            with self.subTest(namespace=namespace):
                state, _request = self._published_chain()
                self._write_event(state, "rq_" + "b" * 64, 1, "accept")
                target = Path(state).resolve() / namespace
                gate_count = []

                def corrupt_on_second_gate(name, args, _count):
                    if name == "lstat" and Path(args[0]) == target:
                        gate_count.append(args[0])
                        if len(gate_count) == 2:
                            self._replace_with(target, "file")
                    return None

                io = RecordingIo(fail=corrupt_on_second_gate)
                self._assert_code(
                    code, lambda: self._store(state, io=io).list())
                target_path = str(target.resolve())
                self.assertEqual(
                    sum(name == "fsync_dir" and path == target_path
                        for name, path in io.calls),
                    1,
                )

    def test_list_second_barrier_failure_stops_before_pass2(self):
        from pathlib import Path

        state, _request = self._published_chain()
        self._write_event(state, "rq_" + "b" * 64, 1, "accept")
        queue = str(Path(state).resolve() / "review_queue")
        queue_fsyncs = []

        def fail_second_barrier(name, args, _count):
            if name == "fsync_dir" and args[0] == queue:
                queue_fsyncs.append(args[0])
                if len(queue_fsyncs) == 2:
                    return OSError("second barrier failed")
            return None

        io = RecordingIo(fail=fail_second_barrier)
        self._assert_code(
            "precommit_directory_fsync_failed",
            lambda: self._store(state, io=io).list())
        self.assertEqual(io.counts["scandir_names"], 2)

    def test_list_tmp_prefix_precedes_type_checks_in_both_namespaces(self):
        import os
        from pathlib import Path

        state, request = self._published_chain()
        decisions = Path(state) / "decisions"
        decisions.mkdir()
        for namespace in (Path(state) / "review_queue", decisions):
            (namespace / ".tmp.directory").mkdir()
            os.symlink(self._state_dir(), namespace / ".tmp.symlink")
        self.assertEqual(self._store(state).list(), {"items": [{
            "rq_id": request["review_queue_item_id"], "state": "pending",
        }]})
        for namespace in (Path(state) / "review_queue", decisions):
            self.assertTrue((namespace / ".tmp.directory").is_dir())
            self.assertTrue((namespace / ".tmp.symlink").is_symlink())

    def test_list_rejects_every_other_illegal_entry_shape(self):
        from pathlib import Path

        cases = (
            ("review_queue", "unknown.json", "file", None),
            ("review_queue", RQ_ID + ".json", "directory", RQ_ID),
            ("review_queue", RQ_ID + ".json", "symlink", RQ_ID),
            ("decisions", "unknown.json", "file", None),
            ("decisions", RQ_ID + ".decision_1.json", "directory", RQ_ID),
            ("decisions", RQ_ID + ".decision_1.json", "symlink", RQ_ID),
        )
        for namespace, name, kind, context in cases:
            with self.subTest(namespace=namespace, name=name, kind=kind):
                state = self._state_dir()
                path = Path(state) / namespace
                path.mkdir()
                entry = path / name
                self._replace_with(entry, kind)
                err = self._assert_code(
                    "malformed_directory_entry",
                    lambda: self._store(state).list())
                self.assertEqual(err.rq_id, context)
                self.assertIsNone(err.decision_seq)

    def test_namespace_terminal_types_have_distinct_codes(self):
        from pathlib import Path

        expected = {
            "review_queue": "malformed_review_queue_namespace",
            "decisions": "malformed_decisions_namespace",
        }
        for namespace, code in expected.items():
            for kind in ("file", "symlink"):
                with self.subTest(namespace=namespace, kind=kind):
                    state, request = self._published_chain()
                    self._replace_with(Path(state) / namespace, kind)
                    err = self._assert_code(
                        code, lambda: self._store(state).list())
                    self.assertIsNone(err.rq_id)
                    if namespace == "review_queue":
                        show_err = self._assert_code(
                            code,
                            lambda: self._store(state).show(
                                request["review_queue_item_id"]))
                        self.assertEqual(
                            show_err.rq_id, request["review_queue_item_id"])


class HR3ShowTests(HR3Fixture):
    """H3.1 RED-11/12 full-G8 show vectors."""

    def test_show_full_g8_error_partition_is_preserved(self):
        import json
        import shutil
        from pathlib import Path

        cases = (
            ("step2", "malformed_request"),
            ("step5", "malformed_request"),
            ("step3", "invalid_review_chain"),
            ("step4", "invalid_review_chain"),
            ("step6", "invalid_review_chain"),
        )
        for step, code in cases:
            with self.subTest(step=step):
                state, request = self._published_chain()
                rq_id = request["review_queue_item_id"]
                rq_path = Path(state) / "review_queue" / (rq_id + ".json")
                if step == "step2":
                    rq_path.write_bytes(rq_path.read_bytes() + b"\n")
                elif step == "step5":
                    value = json.loads(rq_path.read_bytes())
                    value["summary"] += " altered"
                    rq_path.write_bytes(canonical_bytes(value))
                elif step == "step3":
                    shutil.rmtree(Path(state) / "evaluations")
                elif step == "step4":
                    next(Path(state).glob(
                        "evaluations/eval_*/snap_*/reflection.json")).unlink()
                else:
                    path = next(Path(state).glob(
                        "evaluations/eval_*/snap_*/eligibility.json"))
                    value = json.loads(path.read_bytes())
                    value["source_reflection_id"] = "rf_" + "b" * 64
                    path.write_bytes(canonical_bytes(value))
                err = self._assert_code(
                    code, lambda: self._store(state).show(rq_id))
                self.assertEqual(err.rq_id, rq_id)

    def test_show_barrier_failure_is_precommit_directory_fsync_failed(self):
        state, request = self._published_chain()

        def fail(name, _args, _count):
            if name == "fsync_dir":
                return OSError("barrier failed")
            return None

        err = self._assert_code(
            "precommit_directory_fsync_failed",
            lambda: self._store(
                state, io=RecordingIo(fail=fail)).show(
                    request["review_queue_item_id"]))
        self.assertEqual(err.rq_id, request["review_queue_item_id"])
        self.assertIsNone(err.decision_seq)

    def test_show_returns_exact_request_events_and_all_five_states(self):
        cases = {
            "pending": None,
            "deferred": "defer",
            "accepted": "accept",
            "rejected": "reject",
            "revised": "revise",
        }
        for expected, action in cases.items():
            with self.subTest(state=expected):
                state, request = self._published_chain()
                rq_id = request["review_queue_item_id"]
                store = self._store(
                    state, nonce_factory=lambda: "8" * 32,
                    clock_factory=self._fixed_clock)
                if action is not None:
                    self._decide(store, rq_id, action=action)
                result = store.show(rq_id)
                self.assertEqual(set(result), {"request", "events", "state"})
                self.assertEqual(result["request"], request)
                self.assertEqual(len(result["request"]), 12)
                self.assertEqual(result["state"], expected)
                self.assertEqual(
                    [event["decision_seq"] for event in result["events"]],
                    [] if action is None else [1],
                )

    def test_show_and_list_read_seq2_first_but_show_presents_seq_order(self):
        import os

        state, request = self._published_chain()
        rq_id = request["review_queue_item_id"]
        store = self._store(
            state, nonce_factory=lambda: "9" * 32,
            clock_factory=self._fixed_clock)
        self._decide(store, rq_id, action="defer")
        self._decide(store, rq_id, seq=2, action="accept")

        for handle in ("show", "list"):
            with self.subTest(handle=handle):
                io = RecordingIo()
                result = getattr(self._store(state, io=io), handle)(
                    rq_id) if handle == "show" else self._store(
                        state, io=io).list()
                reads = [
                    os.path.basename(path) for name, path in io.calls
                    if name == "read_bytes"
                    and ".decision_" in os.path.basename(path)
                ]
                self.assertEqual(reads[:2], [
                    rq_id + ".decision_2.json",
                    rq_id + ".decision_1.json",
                ])
                if handle == "show":
                    self.assertEqual(
                        [event["decision_seq"] for event in result["events"]],
                        [1, 2],
                    )
                else:
                    self.assertEqual(result, {"items": []})
        # decide has the same order locked by the existing HR.2 test
        # HR2DurabilityAndFaultTests.test_slot_reader_reads_seq2_before_seq1.

    def test_show_g8_completes_before_barrier_with_read_only_operations(self):
        from human_review.review_store import ReviewStore

        state, request = self._published_chain()
        io = RecordingIo()
        builder = self._builder()

        def marked_builder(*args):
            result = builder(*args)
            io.calls.append(("g8_complete", None))
            return result

        store = ReviewStore(
            state, forbidden_roots=(), rq_builder=marked_builder, io=io)
        store.show(request["review_queue_item_id"])
        marker = io.calls.index(("g8_complete", None))
        before = {name for name, _path in io.calls[:marker]}
        self.assertLessEqual(before, {
            "lstat", "resolve_path", "path_limit", "read_bytes",
            "scandir_names",
        })
        self.assertEqual(io.calls[marker + 1][0], "fsync_dir")


class HR3ListContractTests(HR3Fixture):
    """H3.1 RED-11/12 local-Q, filtering, sorting, and purity vectors."""

    def test_list_local_q_five_failures_include_rq_context(self):
        import json
        from pathlib import Path

        def extra_key(value):
            value["extra"] = True

        def wrong_id(value):
            value["review_queue_item_id"] = "rq_" + "b" * 64

        cases = (
            ("canonical", None),
            ("closed_schema", extra_key),
            ("embedded_identity", wrong_id),
            ("schema_version", lambda value: value.update(schema_version=2)),
            ("pending", lambda value: value.update(status="accepted")),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                state, request = self._published_chain()
                rq_id = request["review_queue_item_id"]
                path = Path(state) / "review_queue" / (rq_id + ".json")
                if mutate is None:
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    value = json.loads(path.read_bytes())
                    mutate(value)
                    path.write_bytes(canonical_bytes(value))
                err = self._assert_code(
                    "malformed_request", lambda: self._store(state).list())
                self.assertEqual(err.rq_id, rq_id)
                self.assertIsNone(err.decision_seq)

    def test_list_stops_after_local_q_without_full_chain_reads(self):
        import shutil
        from pathlib import Path

        state, request = self._published_chain()
        shutil.rmtree(Path(state) / "evaluations")

        def forbidden_builder(*_args):
            raise AssertionError("list must not execute G8 steps 3-6")

        from human_review.review_store import ReviewStore
        result = ReviewStore(
            state, forbidden_roots=(), rq_builder=forbidden_builder).list()
        self.assertEqual(result, {"items": [{
            "rq_id": request["review_queue_item_id"], "state": "pending",
        }]})

    def test_list_terminal_filter_and_codepoint_sort_are_explicit(self):
        from pathlib import Path

        state, request = self._published_chain()
        queue = Path(state) / "review_queue"
        for path in queue.iterdir():
            path.unlink()
        vectors = (
            ("0", None),
            ("9", "defer"),
            ("a", None),
            ("1", "accept"),
            ("2", "reject"),
            ("3", "revise"),
        )
        for digit, action in vectors:
            rq_id = "rq_" + digit * 64
            self._write_request(state, self._copy_request(request, rq_id))
            if action is not None:
                self._write_event(state, rq_id, 1, action)
        result = self._store(state, io=ReverseScandirIo()).list()
        self.assertEqual(result, {"items": [
            {"rq_id": "rq_" + "0" * 64, "state": "pending"},
            {"rq_id": "rq_" + "9" * 64, "state": "deferred"},
            {"rq_id": "rq_" + "a" * 64, "state": "pending"},
        ]})

    def test_list_empty_set_is_legal(self):
        self.assertEqual(self._store(self._state_dir()).list(), {"items": []})

    def test_unsafe_path_and_path_too_long_cover_all_three_handles(self):
        import os
        import shutil
        from pathlib import Path

        for handle in ("decide", "show"):
            with self.subTest(code="unsafe_path", handle=handle):
                state, request = self._published_chain()
                evaluations = Path(state) / "evaluations"
                shutil.rmtree(evaluations)
                os.symlink(self._state_dir(), evaluations)
                store = self._store(state, clock_factory=self._fixed_clock)
                call = (lambda: self._decide(
                    store, request["review_queue_item_id"])) \
                    if handle == "decide" else \
                    (lambda: store.show(request["review_queue_item_id"]))
                self._assert_code("unsafe_path", call)

        with self.subTest(code="unsafe_path", handle="list"):
            parent = Path(self._state_dir())
            real = parent / "real"
            real.mkdir()
            state = real / "state"
            state.mkdir()
            alias = parent / "alias"
            os.symlink(real, alias)
            self._assert_code(
                "unsafe_path", lambda: self._store(alias / "state").list())

        for handle in ("decide", "show", "list"):
            with self.subTest(code="path_too_long", handle=handle):
                state, request = self._published_chain()
                rq_id = request["review_queue_item_id"]

                def limit(path, handle=handle):
                    basename = os.path.basename(path)
                    target = (
                        handle == "decide"
                        and basename == rq_id + ".decision_1.json"
                    ) or (
                        handle == "show" and basename == rq_id + ".json"
                    ) or (
                        handle == "list" and basename == "review_queue"
                    )
                    return len(os.fsencode(path)) - 1 if target else 100000

                store = self._store(
                    state, io=RecordingIo(path_limit=limit),
                    clock_factory=self._fixed_clock)
                if handle == "decide":
                    call = lambda: self._decide(store, rq_id)
                elif handle == "show":
                    call = lambda: store.show(rq_id)
                else:
                    call = store.list
                self._assert_code("path_too_long", call)

    def test_list_never_calls_mutating_io_operations(self):
        state, _request = self._published_chain()
        io = RecordingIo()
        self._store(state, io=io).list()
        names = {name for name, _path in io.calls}
        self.assertTrue(names.isdisjoint({
            "open_exclusive", "write_all", "mkdir", "link", "unlink",
        }))


class HR3AgentTests(HR3Fixture):
    """H3.2 RED-12 agent and independent registry vectors."""

    def test_list_dispatch_is_a_direct_base_agent_result(self):
        from unittest.mock import patch

        from agents.base import BaseAgent
        from agents.human_review import HumanReviewAgent

        agent = HumanReviewAgent(self._store(self._state_dir()))
        self.assertIsInstance(agent, BaseAgent)
        self.assertEqual(agent.agent_id, "human_review_agent")
        self.assertEqual(
            agent.handles,
            ["review.list", "review.show", "review.decide"],
        )
        with patch.object(
                BaseAgent, "result",
                side_effect=AssertionError("result() must not be called")):
            self.assertEqual(
                agent.run({"type": "review.list", "input": {}}, {}),
                {"items": []},
            )

    def test_show_and_decide_dispatch_preserve_success_shapes(self):
        from agents.human_review import HumanReviewAgent

        state, request = self._published_chain()
        rq_id = request["review_queue_item_id"]
        agent = HumanReviewAgent(self._store(
            state,
            nonce_factory=lambda: "a" * 32,
            clock_factory=self._fixed_clock,
        ))
        shown = agent.run({
            "type": "review.show",
            "input": {"rq_id": rq_id},
        }, {})
        self.assertEqual(shown, {
            "request": request,
            "events": [],
            "state": "pending",
        })
        decided = agent.run({
            "type": "review.decide",
            "input": {
                "rq_id": rq_id,
                "decision_seq": 1,
                "action": "accept",
                "operator_claim": "operator",
                "reason": "evidence sufficient",
            },
        }, {})
        self.assertEqual(decided["status"], "published")
        self.assertEqual(decided["event"]["review_queue_item_id"], rq_id)

    def test_task_and_input_envelopes_are_closed_before_field_parsing(self):
        from agents.human_review import HumanReviewAgent
        from human_review.review_store import ReviewError

        agent = HumanReviewAgent(self._store(self._state_dir()))
        invalid = (
            {"type": "review.list"},
            {"type": "review.list", "input": [], "trace_id": "x"},
            {"type": "review.list", "input": {"extra": True}},
            {"type": "review.show", "input": {"rq_id": RQ_ID, "x": 1}},
            {"type": "review.decide", "input": {"unknown": True}},
            {"type": "review.unknown", "input": {}},
        )
        for task in invalid:
            with self.subTest(task=task):
                with self.assertRaises(ReviewError) as caught:
                    agent.run(task, {})
                self.assertEqual(caught.exception.code, "invalid_request")
                self.assertIsNone(caught.exception.rq_id)
                self.assertIsNone(caught.exception.decision_seq)

        with self.assertRaises(ReviewError) as caught:
            agent.run({"type": "review.show", "input": {}}, {})
        self.assertEqual(
            caught.exception.code, "invalid_review_queue_item_id")

    def test_review_error_is_re_raised_without_wrapping(self):
        from agents.human_review import HumanReviewAgent
        from human_review.review_store import ReviewError

        expected = ReviewError("request_not_found", rq_id=RQ_ID)

        class FailingStore:
            def show(self, _rq_id):
                raise expected

        agent = HumanReviewAgent(FailingStore())
        with self.assertRaises(ReviewError) as caught:
            agent.run({"type": "review.show", "input": {"rq_id": RQ_ID}}, {})
        self.assertIs(caught.exception, expected)

    def test_independent_registry_selects_and_runs_the_supplied_agent(self):
        from agents.human_review import HumanReviewAgent
        from agents.registry import AgentRegistry

        agent = HumanReviewAgent(self._store(self._state_dir()))
        registry = AgentRegistry.from_config(
            SOURCE_DIR / "agents" / "registry-review.yaml",
            [agent],
        )
        for handle in agent.handles:
            with self.subTest(handle=handle):
                self.assertIs(registry.select({"type": handle}), agent)
        self.assertEqual(
            registry.select({"type": "review.list"}).run(
                {"type": "review.list", "input": {}}, {}),
            {"items": []},
        )


class HR3CliTests(HR3Fixture):
    """H3.3 RED-12/16 early dispatch and envelope vectors."""

    def _run_main(self, task, *, state=None, root=None, extra=()):
        import contextlib
        import io
        import json

        import laos

        state = self._state_dir() if state is None else state
        root = self._state_dir() if root is None else root
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "--root", str(root),
            "--state-dir", str(state),
            "--task-json", json.dumps(task, separators=(",", ":")),
            *extra,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return laos.main(argv), stdout.getvalue(), stderr.getvalue()

    def test_review_list_routes_before_broken_model_configuration(self):
        from pathlib import Path

        model_config = Path(self._state_dir()) / "broken-model.json"
        model_config.write_text("{", encoding="utf-8")
        code, stdout, stderr = self._run_main(
            {"type": "review.list", "input": {}},
            extra=("--model-config", str(model_config)),
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, '{"items":[]}\n')

    def test_review_handles_are_context_free_in_depth(self):
        from agents.orchestrator import ContextAgent

        class Builder:
            def __init__(self):
                self.empty_calls = []

            def build(self, *_args):
                raise AssertionError("review tasks must not read memory context")

            def empty(self, limit):
                self.empty_calls.append(limit)
                return {"sources": [], "limit": limit}

        builder = Builder()
        agent = ContextAgent(builder)
        for handle in ("review.list", "review.show", "review.decide"):
            with self.subTest(handle=handle):
                result = agent.run({
                    "type": "context.build",
                    "input": {"task": {"type": handle, "input": {}}},
                }, {})
                self.assertEqual(result["output"]["sources"], [])
        self.assertEqual(builder.empty_calls, [8000, 8000, 8000])

    def test_hr_error_envelope_is_closed_and_uses_parsed_context(self):
        import json

        cases = (
            (
                {"type": "review.show", "input": {}},
                {"category": "precondition", "code":
                 "invalid_review_queue_item_id"},
            ),
            (
                {"type": "review.show", "input": {"rq_id": RQ_ID}},
                {"category": "jurisdiction", "code": "request_not_found",
                 "rq_id": RQ_ID},
            ),
            (
                {"type": "review.decide", "input": {
                    "rq_id": RQ_ID, "decision_seq": 0,
                }},
                {"category": "precondition", "code":
                 "invalid_decision_seq", "rq_id": RQ_ID},
            ),
            (
                {"type": "review.decide", "input": {
                    "rq_id": RQ_ID, "decision_seq": 1,
                    "action": "unknown", "operator_claim": "operator",
                    "reason": "evidence",
                }},
                {"category": "precondition", "code": "invalid_action",
                 "rq_id": RQ_ID, "decision_seq": 1},
            ),
        )
        for task, expected in cases:
            with self.subTest(code=expected["code"]):
                code, stdout, stderr = self._run_main(task)
                self.assertEqual(code, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr), {"error": expected})

    def test_missing_state_dir_precedes_review_bootstrap(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch

        import laos

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "--root", self._state_dir(),
            "--task-json", json.dumps(
                {"type": "review.list", "input": {}}),
        ]
        with patch.object(
                laos, "_pin_and_load_rq_builder",
                side_effect=AssertionError("pinning must not run")) as pin:
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                code = laos.main(argv)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        error = json.loads(stderr.getvalue())["error"]
        self.assertEqual(set(error), {"code", "message", "stage"})
        self.assertEqual(error["code"], "request_failed")
        pin.assert_not_called()

    def test_pinning_reuses_trusted_modules_and_rejects_foreign_src(self):
        import types

        import laos

        builder = laos._pin_and_load_rq_builder()
        trusted_src = sys.modules["src"]
        trusted_loop = sys.modules["src.learning_loop"]
        self.assertIs(laos._pin_and_load_rq_builder(), builder)
        self.assertIs(sys.modules["src"], trusted_src)
        self.assertIs(sys.modules["src.learning_loop"], trusted_loop)
        self.assertEqual(builder.__module__, "src.learning_loop.queue_request")

        saved = {
            name: module for name, module in tuple(sys.modules.items())
            if name == "src" or name.startswith("src.")
        }
        for name in saved:
            sys.modules.pop(name, None)
        foreign = types.ModuleType("src")
        foreign.__path__ = ["/foreign/src"]
        sys.modules["src"] = foreign
        try:
            with self.assertRaisesRegex(ValueError, "untrusted src namespace"):
                laos._pin_and_load_rq_builder()
            self.assertIs(sys.modules["src"], foreign)
            self.assertNotIn("src.learning_loop.queue_request", sys.modules)
        finally:
            for name in tuple(sys.modules):
                if name == "src" or name.startswith("src."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)

    def test_cli_transport_runs_show_and_decide_through_registry(self):
        import json

        state, request = self._published_chain()
        root = self._state_dir()
        rq_id = request["review_queue_item_id"]
        code, stdout, stderr = self._run_main(
            {"type": "review.show", "input": {"rq_id": rq_id}},
            state=state,
            root=root,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), {
            "request": request, "events": [], "state": "pending",
        })

        code, stdout, stderr = self._run_main({
            "type": "review.decide",
            "input": {
                "rq_id": rq_id,
                "decision_seq": 1,
                "action": "accept",
                "operator_claim": "operator",
                "reason": "evidence sufficient",
            },
        }, state=state, root=root)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "published")

    def test_cli_invalid_state_dir_uses_hr_envelope_without_context(self):
        import json
        from pathlib import Path

        missing = Path(self._state_dir()) / "missing"
        code, stdout, stderr = self._run_main(
            {"type": "review.list", "input": {}},
            state=missing,
        )
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {
            "category": "precondition",
            "code": "invalid_state_dir",
        }})


class HR3TransportE2ETests(HR3Fixture):
    """H3.4 RED-16 subprocess and construction-boundary vectors."""

    def test_subprocess_pinning_blocks_malicious_src_package(self):
        import json
        import os
        import subprocess
        from pathlib import Path

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        root.mkdir()
        state.mkdir()
        malicious = base / "malicious"
        fake_src = malicious / "src"
        fake_src.mkdir(parents=True)
        sentinel = base / "malicious-src-executed"
        diagnostic = base / "pinning-diagnostic.json"
        (fake_src / "__init__.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('executed')\n"
            "foreign_marker = True\n",
            encoding="utf-8",
        )
        (malicious / "sitecustomize.py").write_text(
            "import atexit, json, sys\n"
            "from pathlib import Path\n"
            f"_diagnostic = Path({str(diagnostic)!r})\n"
            "def _dump():\n"
            "    src = sys.modules.get('src')\n"
            "    loop = sys.modules.get('src.learning_loop')\n"
            "    queue = sys.modules.get('src.learning_loop.queue_request')\n"
            "    builder = getattr(queue, 'build_review_queue_request', None)\n"
            "    value = {\n"
            "        'src_paths': list(getattr(src, '__path__', ())),\n"
            "        'foreign_marker': getattr(src, 'foreign_marker', False),\n"
            "        'loop_file': getattr(loop, '__file__', None),\n"
            "        'queue_file': getattr(queue, '__file__', None),\n"
            "        'builder_module': getattr(builder, '__module__', None),\n"
            "    }\n"
            "    _diagnostic.write_text(json.dumps(value), encoding='utf-8')\n"
            "atexit.register(_dump)\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(malicious)
        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE_DIR / "laos.py"),
                "--root", str(root),
                "--state-dir", str(state),
                "--task-json", json.dumps(
                    {"type": "review.list", "input": {}},
                    separators=(",", ":"),
                ),
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '{"items":[]}\n')
        self.assertEqual(result.stderr, "")
        self.assertFalse(sentinel.exists())
        identity = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(
            {Path(path).resolve() for path in identity["src_paths"]},
            {SOURCE_DIR.resolve()},
        )
        self.assertFalse(identity["foreign_marker"])
        self.assertEqual(
            Path(identity["loop_file"]).resolve(),
            (SOURCE_DIR / "learning_loop" / "__init__.py").resolve(),
        )
        self.assertEqual(
            Path(identity["queue_file"]).resolve(),
            (SOURCE_DIR / "learning_loop" / "queue_request.py").resolve(),
        )
        self.assertEqual(
            identity["builder_module"],
            "src.learning_loop.queue_request",
        )

    def test_subprocess_review_contract_and_broken_model_config(self):
        import json
        import subprocess
        from pathlib import Path

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        root.mkdir()
        state.mkdir()

        def run(task, *extra):
            return subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_DIR / "laos.py"),
                    "--root", str(root),
                    "--state-dir", str(state),
                    "--task-json", json.dumps(
                        task, separators=(",", ":")),
                    *extra,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        invalid = (
            {"type": "review.list", "input": []},
            {"type": "review.list", "input": {"unknown": True}},
            {"type": "review.list", "input": {}, "trace_id": "x"},
            {"type": "review.list"},
        )
        for task in invalid:
            with self.subTest(task=task):
                result = run(task)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(json.loads(result.stderr), {"error": {
                    "category": "precondition",
                    "code": "invalid_request",
                }})

        broken = base / "broken-model.json"
        broken.write_text("{", encoding="utf-8")
        result = run(
            {"type": "review.list", "input": {}},
            "--model-config", str(broken),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '{"items":[]}\n')
        self.assertEqual(result.stderr, "")
        self.assertEqual(self._tree_bytes(root), {})
        self.assertEqual(self._tree_bytes(state), {})

    def test_pinning_rejects_foreign_module_without_replacement_or_import(self):
        import json
        import subprocess
        from pathlib import Path

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        root.mkdir()
        state.mkdir()
        argv = [
            "--root", str(root),
            "--state-dir", str(state),
            "--task-json", json.dumps(
                {"type": "review.list", "input": {}},
                separators=(",", ":"),
            ),
        ]
        script = (
            "import json, sys, types\n"
            f"sys.path.insert(0, {str(SOURCE_DIR)!r})\n"
            "import laos\n"
            "foreign = types.ModuleType('src')\n"
            "foreign.__path__ = ['/foreign/src']\n"
            "sys.modules['src'] = foreign\n"
            f"code = laos.main({argv!r})\n"
            "print(json.dumps({\n"
            "    'same': sys.modules.get('src') is foreign,\n"
            "    'queue_loaded': 'src.learning_loop.queue_request' in sys.modules,\n"
            "    'store_loaded': 'human_review.review_store' in sys.modules,\n"
            "    'agent_loaded': 'agents.human_review' in sys.modules,\n"
            "}, sort_keys=True))\n"
            "raise SystemExit(code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {
            "agent_loaded": False,
            "queue_loaded": False,
            "same": True,
            "store_loaded": False,
        })
        error = json.loads(result.stderr)["error"]
        self.assertEqual(set(error), {"code", "message", "stage"})
        self.assertEqual(error["code"], "request_failed")
        self.assertEqual(self._tree_bytes(root), {})
        self.assertEqual(self._tree_bytes(state), {})

    def test_pinning_reuses_preloaded_trusted_modules_through_cli_main(self):
        import json
        import subprocess
        from pathlib import Path

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        diagnostic = base / "trusted-diagnostic.json"
        root.mkdir()
        state.mkdir()
        argv = [
            "--root", str(root),
            "--state-dir", str(state),
            "--task-json", json.dumps(
                {"type": "review.list", "input": {}},
                separators=(",", ":"),
            ),
        ]
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(SOURCE_DIR)!r})\n"
            "import src.learning_loop.queue_request as queue\n"
            "trusted_src = sys.modules['src']\n"
            "trusted_loop = sys.modules['src.learning_loop']\n"
            "import laos\n"
            f"code = laos.main({argv!r})\n"
            "value = {\n"
            "    'same_src': sys.modules['src'] is trusted_src,\n"
            "    'same_loop': sys.modules['src.learning_loop'] is trusted_loop,\n"
            "    'src_paths': list(sys.modules['src'].__path__),\n"
            "    'loop_file': trusted_loop.__file__,\n"
            "    'queue_file': queue.__file__,\n"
            "    'builder_module': queue.build_review_queue_request.__module__,\n"
            "}\n"
            f"Path({str(diagnostic)!r}).write_text(json.dumps(value), encoding='utf-8')\n"
            "raise SystemExit(code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '{"items":[]}\n')
        identity = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertTrue(identity["same_src"])
        self.assertTrue(identity["same_loop"])
        self.assertEqual(
            {Path(path).resolve() for path in identity["src_paths"]},
            {SOURCE_DIR.resolve()},
        )
        self.assertEqual(
            Path(identity["loop_file"]).resolve(),
            (SOURCE_DIR / "learning_loop" / "__init__.py").resolve(),
        )
        self.assertEqual(
            Path(identity["queue_file"]).resolve(),
            (SOURCE_DIR / "learning_loop" / "queue_request.py").resolve(),
        )
        self.assertEqual(
            identity["builder_module"],
            "src.learning_loop.queue_request",
        )

    def test_review_modules_are_lazy_and_state_dir_error_has_priority(self):
        import builtins
        import contextlib
        import io
        import json
        import subprocess
        from pathlib import Path
        from unittest.mock import patch

        import laos

        calls = []
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name in ("human_review.review_store", "agents.human_review"):
                calls.append(name)
                raise AssertionError("review import must remain lazy")
            return original_import(name, *args, **kwargs)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("builtins.__import__", side_effect=guarded_import):
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                code = laos.main([
                    "--root", self._state_dir(),
                    "--task-json", json.dumps(
                        {"type": "review.list", "input": {}}),
                ])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            set(json.loads(stderr.getvalue())["error"]),
            {"code", "message", "stage"},
        )

        base = Path(self._state_dir())
        diagnostic = base / "lazy.json"
        missing_root = base / "missing-root"
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(SOURCE_DIR)!r})\n"
            "import laos\n"
            "before = {name: name in sys.modules for name in "
            "('human_review.review_store', 'agents.human_review')}\n"
            "code = laos.main([\n"
            f"    '--root', {str(missing_root)!r},\n"
            "    '--task-json', '{\"type\":\"unknown\",\"input\":{}}',\n"
            "])\n"
            "after = {name: name in sys.modules for name in before}\n"
            f"Path({str(diagnostic)!r}).write_text(json.dumps([before, after]), encoding='utf-8')\n"
            "raise SystemExit(code)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        before, after = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(before, {
            "agents.human_review": False,
            "human_review.review_store": False,
        })
        self.assertEqual(after, before)

    def test_registry_config_failures_are_generic_and_side_effect_free(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch

        import laos

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        fake_src = base / "fake-src"
        config = fake_src / "agents" / "registry-review.yaml"
        root.mkdir()
        state.mkdir()
        config.parent.mkdir(parents=True)
        valid_entry = {
            "id": "human_review_agent",
            "class": "HumanReviewAgent",
            "handles": ["review.list", "review.show", "review.decide"],
        }
        variants = (
            ("missing", None),
            ("invalid-json", "{"),
            ("handles", json.dumps({"agents": [{
                **valid_entry, "handles": ["review.list"],
            }]})),
            ("class", json.dumps({"agents": [{
                **valid_entry, "class": "ForeignAgent",
            }]})),
        )
        argv = [
            "--root", str(root),
            "--state-dir", str(state),
            "--task-json", '{"type":"review.list","input":{}}',
        ]
        for label, value in variants:
            with self.subTest(label=label):
                config.unlink(missing_ok=True)
                if value is not None:
                    config.write_text(value, encoding="utf-8")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with patch.object(
                        laos, "__file__", str(fake_src / "laos.py")), \
                        patch.object(
                            laos, "_pin_and_load_rq_builder",
                            return_value=self._builder()), \
                        patch.object(laos, "build_application") as app, \
                        patch.object(laos, "build_model_backend") as model, \
                        patch.object(laos, "MemoryStore") as memory_store, \
                        patch.object(laos, "CandidateStore") as candidates, \
                        patch.object(
                            laos, "ConversationReviewService") as review, \
                        patch.object(laos, "ContextAgent") as context_agent:
                    with contextlib.redirect_stdout(stdout), \
                            contextlib.redirect_stderr(stderr):
                        code = laos.main(argv)
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue(), "")
                error = json.loads(stderr.getvalue())["error"]
                self.assertEqual(set(error), {"code", "message", "stage"})
                self.assertEqual(error["code"], "request_failed")
                app.assert_not_called()
                model.assert_not_called()
                memory_store.assert_not_called()
                candidates.assert_not_called()
                review.assert_not_called()
                context_agent.assert_not_called()
                self.assertEqual(self._tree_bytes(root), {})
                self.assertEqual(self._tree_bytes(state), {})

    def test_review_dispatch_bypasses_generic_construction_and_generic_app(self):
        import argparse
        import contextlib
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch

        import laos
        import memory

        base = Path(self._state_dir())
        root = base / "root"
        state = base / "state"
        root.mkdir()
        state.mkdir()
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "--root", str(root),
            "--state-dir", str(state),
            "--task-json", json.dumps(
                {"type": "review.list", "input": {}}),
        ]
        with patch.object(laos, "build_application") as app, \
                patch.object(laos, "build_model_backend") as model, \
                patch.object(laos, "MemoryStore") as memory_store, \
                patch.object(laos, "CandidateStore") as candidates, \
                patch.object(
                    laos, "ConversationReviewService") as review_service, \
                patch.object(laos, "ContextAgent") as context_agent:
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                code = laos.main(argv)
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), '{"items":[]}\n')
        app.assert_not_called()
        model.assert_not_called()
        memory_store.assert_not_called()
        candidates.assert_not_called()
        review_service.assert_not_called()
        context_agent.assert_not_called()

        generic_root = base / "generic-root"
        generic_state = base / "generic-state"
        memory.init_store(generic_root)
        memory.db_init(argparse.Namespace(
            root=str(generic_root), state_dir=str(generic_state)))

        class Backend:
            def review(self, _conversation):
                return {}

        application = laos.build_application(
            generic_root, generic_state, Backend())
        self.assertNotIn(
            "human_review_agent", application.registry._agents_by_id)
        from memory.store import MemoryStore
        with patch.object(
                MemoryStore, "active_relevant",
                side_effect=AssertionError("review task read active memory")):
            with self.assertRaises(ValueError) as caught:
                application.run({"type": "review.list", "input": {}})
        self.assertEqual(str(caught.exception), "task type is not registered")

    def test_all_frozen_codes_are_emitted_and_capabilities_are_closed(self):
        import ast
        import subprocess

        from human_review.decision import ERROR_CODES

        production = (
            SOURCE_DIR / "human_review" / "review_store.py",
            SOURCE_DIR / "agents" / "human_review.py",
        )
        emitted = set()
        for path in production:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else None
                arg = node.args[0]
                if name in ("_raise", "ReviewError") \
                        and isinstance(arg, ast.Constant) \
                        and isinstance(arg.value, str):
                    emitted.add(arg.value)
        self.assertEqual(emitted, set(ERROR_CODES))
        self.assertNotIn("concurrent_change", emitted)

        banned = (
            "learning_loop", "src.learning_loop", "memory", "src.memory",
            "policy", "promotion", "lifecycle",
        )
        audited = tuple((SOURCE_DIR / "human_review").glob("*.py")) + (
            SOURCE_DIR / "agents" / "human_review.py",
        )
        for path in audited:
            imports = set()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            with self.subTest(path=path):
                self.assertTrue(all(
                    not name.startswith(banned) for name in imports),
                    imports,
                )

        decision = SOURCE_DIR / "human_review" / "decision.py"
        decision_tree = ast.parse(decision.read_text(encoding="utf-8"))
        io_imports = {"os", "pathlib", "io", "shutil", "subprocess"}
        self.assertFalse(any(
            isinstance(node, ast.Import)
            and any(alias.name.split(".")[0] in io_imports
                    for alias in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[0] in io_imports
            for node in ast.walk(decision_tree)
        ))
        script = (
            "import builtins, sys\n"
            f"sys.path.insert(0, {str(SOURCE_DIR)!r})\n"
            "from human_review.decision import build_decision, reduce_state, validate_schema\n"
            "def fail(*args, **kwargs): raise AssertionError('I/O attempted')\n"
            "builtins.open = fail\n"
            f"event = build_decision({RQ_ID!r}, 1, 'accept', 'operator', 'reason', {RECORDED_AT!r})\n"
            "validate_schema(event)\n"
            "assert reduce_state([event]) == 'accepted'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_projects_every_category_and_all_post_link_codes(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch

        import laos
        from human_review.decision import ERROR_CATEGORIES
        from human_review.review_store import ReviewError

        codes = (
            "invalid_review_chain",
            "invalid_action",
            "decision_slot_conflict",
            "decision_final_byte_mismatch",
            "decision_final_missing",
            "malformed_decision",
            "decision_directory_fsync_failed",
            "final_readback_failed",
            "link_failed",
        )

        class Agent:
            def __init__(self, error):
                self.error = error

            def run(self, task, context):
                raise self.error

        class Registry:
            def __init__(self, error):
                self.agent = Agent(error)

            def select(self, task):
                return self.agent

        root = self._state_dir()
        state = self._state_dir()
        argv = [
            "--root", root,
            "--state-dir", state,
            "--task-json", '{"type":"review.decide","input":{}}',
        ]
        seen_categories = set()
        for code_name in codes:
            with self.subTest(code=code_name):
                stdout = io.StringIO()
                stderr = io.StringIO()
                error = ReviewError(
                    code_name, rq_id=RQ_ID, decision_seq=1)
                with patch.object(
                        laos, "_pin_and_load_rq_builder",
                        return_value=self._builder()), \
                        patch.object(
                            laos, "build_review_transport",
                            return_value=Registry(error)):
                    with contextlib.redirect_stdout(stdout), \
                            contextlib.redirect_stderr(stderr):
                        result = laos.main(argv)
                self.assertEqual(result, 1)
                self.assertEqual(stdout.getvalue(), "")
                projected = json.loads(stderr.getvalue())
                self.assertEqual(projected, {"error": {
                    "category": ERROR_CATEGORIES[code_name],
                    "code": code_name,
                    "rq_id": RQ_ID,
                    "decision_seq": 1,
                }})
                seen_categories.add(ERROR_CATEGORIES[code_name])
        self.assertEqual(seen_categories, {
            "jurisdiction", "precondition", "conflict", "corruption",
            "durability_unknown", "io",
        })

    def test_phase5_and_human_review_ignore_each_others_temp_files(self):
        from pathlib import Path

        from src.learning_loop.enrichment import (
            build_enriched_utility_evaluation,
        )
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop.persistence import (
            persist_learning_chain,
            resume_learning_chain,
        )
        from src.learning_loop.reflection_builder import build_reflection

        state = Path(self._state_dir())
        decisions = state / "decisions"
        decisions.mkdir()
        hr_temp = decisions / (
            ".tmp." + RQ_ID + ".decision_1.json." + "1" * 32)
        hr_bytes = b"human-review-owned-temp"
        hr_temp.write_bytes(hr_bytes)

        enriched = build_enriched_utility_evaluation(
            evaluate_experiment_bundle(self._bundle()))
        digest = build_reflection(enriched)["source_snapshot_digest"]
        persisted = persist_learning_chain(state, enriched)
        resume_learning_chain(
            state, enriched["source_evaluation_id"], digest)
        self.assertTrue(hr_temp.exists())
        self.assertEqual(hr_temp.read_bytes(), hr_bytes)

        queue = state / "review_queue"
        p5_temp = queue / ".tmp.phase5-retry-residue"
        p5_bytes = b"phase-five-owned-temp"
        p5_temp.write_bytes(p5_bytes)
        listed = self._store(state).list()
        self.assertEqual(
            [item["rq_id"] for item in listed["items"]],
            [persisted["review_queue_request"]["review_queue_item_id"]],
        )
        self.assertTrue(p5_temp.exists())
        self.assertEqual(p5_temp.read_bytes(), p5_bytes)


class HR3CloseoutAuditTests(HR3Fixture):
    """H3.5 mechanical closure for the frozen RED-13/14/15 vectors."""

    def test_operator_claim_and_reason_errors_reach_cli_without_effects(self):
        import contextlib
        import io
        import json

        import laos

        missing = object()
        cases = (
            ("invalid_operator_claim", "operator_claim", missing),
            ("invalid_operator_claim", "operator_claim", None),
            ("invalid_operator_claim", "operator_claim", ""),
            ("invalid_operator_claim", "operator_claim", 1),
            ("invalid_reason", "reason", missing),
            ("invalid_reason", "reason", None),
            ("invalid_reason", "reason", ""),
            ("invalid_reason", "reason", 1),
        )
        for expected, field, value in cases:
            with self.subTest(code=expected, value=value):
                state, request = self._published_chain()
                root = self._state_dir()
                rq_id = request["review_queue_item_id"]
                values = {
                    "rq_id": rq_id,
                    "decision_seq": 1,
                    "action": "accept",
                    "operator_claim": "operator",
                    "reason": "evidence sufficient",
                }
                if value is missing:
                    values.pop(field)
                else:
                    values[field] = value
                before = self._tree_bytes(state)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), \
                        contextlib.redirect_stderr(stderr):
                    code = laos.main([
                        "--root", root,
                        "--state-dir", state,
                        "--task-json", json.dumps({
                            "type": "review.decide", "input": values,
                        }, separators=(",", ":")),
                    ])
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(json.loads(stderr.getvalue()), {"error": {
                    "category": "precondition",
                    "code": expected,
                    "rq_id": rq_id,
                    "decision_seq": 1,
                }})
                self.assertEqual(self._tree_bytes(state), before)

    def test_all_post_link_errors_converge_by_retry_or_show(self):
        import contextlib
        import os
        from pathlib import Path
        from unittest.mock import patch

        from human_review.decision import DecisionSchemaError

        cases = (
            ("decision_directory_fsync_failed", "valid"),
            ("final_readback_failed", "valid"),
            ("malformed_decision", "valid"),
            ("decision_final_missing", "absent"),
            ("decision_final_byte_mismatch", "corrupt"),
        )
        for expected, physical in cases:
            with self.subTest(code=expected, physical=physical):
                state, request = self._published_chain()
                rq_id = request["review_queue_item_id"]
                decisions = Path(state).resolve() / "decisions"
                final = decisions / (rq_id + ".decision_1.json")

                def fail(name, args, _count, expected=expected):
                    if expected == "decision_directory_fsync_failed" \
                            and name == "fsync_dir" \
                            and Path(args[0]) == decisions:
                        return OSError("post-link fsync failed")
                    return None

                def read_hook(path, _count, expected=expected):
                    if Path(path) != final:
                        return _DELEGATE
                    if expected == "final_readback_failed":
                        raise OSError("post-link read failed")
                    if expected == "decision_final_missing":
                        os.unlink(path)
                        raise FileNotFoundError(path)
                    if expected == "decision_final_byte_mismatch":
                        Path(path).write_bytes(b"corrupt-final")
                        return b"corrupt-final"
                    return _DELEGATE

                active_io = RecordingIo(fail=fail, read_hook=read_hook)
                decoder = patch(
                    "human_review.review_store.decode_event",
                    side_effect=DecisionSchemaError(
                        "post-link decode failure"),
                ) if expected == "malformed_decision" \
                    else contextlib.nullcontext()
                with decoder:
                    error = self._assert_code(
                        expected,
                        lambda: self._decide(
                            self._store(
                                state,
                                io=active_io,
                                nonce_factory=lambda: "a" * 32,
                                clock_factory=self._fixed_clock,
                            ),
                            rq_id,
                        ),
                    )
                self.assertEqual(error.rq_id, rq_id)
                self.assertEqual(error.decision_seq, 1)
                self.assertTrue(all(
                    Path(path).name.startswith(".tmp.")
                    for name, path in active_io.calls if name == "unlink"
                ))

                store = self._store(
                    state,
                    nonce_factory=lambda: "b" * 32,
                    clock_factory=self._fixed_clock,
                )
                if physical == "valid":
                    self.assertTrue(final.is_file())
                    before_retry = final.read_bytes()
                    retry = self._decide(store, rq_id)
                    self.assertEqual(retry["status"], "idempotent")
                    self.assertEqual(final.read_bytes(), before_retry)
                    self.assertEqual(store.show(rq_id)["state"], "accepted")
                elif physical == "absent":
                    self.assertFalse(final.exists())
                    retry = self._decide(store, rq_id)
                    self.assertEqual(retry["status"], "published")
                    self.assertEqual(store.show(rq_id)["state"], "accepted")
                else:
                    self.assertEqual(final.read_bytes(), b"corrupt-final")
                    self._assert_code(
                        "malformed_decision",
                        lambda: self._decide(store, rq_id),
                    )
                    self._assert_code(
                        "malformed_decision", lambda: store.show(rq_id))
                    self.assertEqual(final.read_bytes(), b"corrupt-final")

    def test_p3_static_matrix_and_producer_dynamic_guard(self):
        import ast
        import os
        from pathlib import Path
        from unittest.mock import patch

        from src.learning_loop.enrichment import (
            build_enriched_utility_evaluation,
        )
        from src.learning_loop.evaluation import evaluate_experiment_bundle
        from src.learning_loop import persistence
        from src.learning_loop.reflection_builder import build_reflection

        producer_paths = tuple(
            SOURCE_DIR / "learning_loop" / name for name in (
                "evaluation.py", "enrichment.py", "reflection_builder.py",
                "eligibility.py", "queue_request.py", "persistence.py",
            )
        )
        producer_forbidden = (
            "human_review", "src.human_review", "memory", "src.memory",
            "policy", "promotion", "lifecycle",
        )
        review_forbidden = (
            "learning_loop", "src.learning_loop", "memory", "src.memory",
            "policy", "promotion", "lifecycle",
        )

        def imported_modules(path):
            found = set()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            return found

        for path in producer_paths:
            with self.subTest(subject="producer", path=path):
                self.assertTrue(all(
                    not name.startswith(producer_forbidden)
                    for name in imported_modules(path)
                ))

        human_review_paths = tuple(
            (SOURCE_DIR / "human_review").glob("*.py")) + (
                SOURCE_DIR / "agents" / "human_review.py",
            )
        for path in human_review_paths:
            with self.subTest(subject="human-review", path=path):
                self.assertTrue(all(
                    not name.startswith(review_forbidden)
                    for name in imported_modules(path)
                ))

        laos_path = SOURCE_DIR / "laos.py"
        laos_tree = ast.parse(laos_path.read_text(encoding="utf-8"))
        direct_store_calls = {
            node.func.attr
            for node in ast.walk(laos_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"list", "show", "decide"}
        }
        self.assertEqual(direct_store_calls, set())
        self.assertFalse({"evaluations", "review_queue", "decisions"} & {
            node.value for node in ast.walk(laos_tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        })

        class GuardIo:
            def __init__(self):
                self.base = persistence._Io()
                self.calls = []

            def __getattr__(self, name):
                function = getattr(self.base, name)

                def guarded(*args):
                    for value in args:
                        if isinstance(value, (str, os.PathLike)) \
                                and "decisions" in Path(value).parts:
                            raise AssertionError(
                                "Phase 5 producer touched decisions")
                    self.calls.append((name, args[0] if args else None))
                    return function(*args)

                return guarded

        state = Path(self._state_dir())
        decisions = state / "decisions"
        decisions.mkdir()
        protected = {
            decisions / "producer-must-not-touch": b"decision-sentinel",
            state / "memory-state": b"memory-sentinel",
            state / "policy-state": b"policy-sentinel",
            state / "promotion-state": b"promotion-sentinel",
        }
        for path, value in protected.items():
            path.write_bytes(value)
        enriched = build_enriched_utility_evaluation(
            evaluate_experiment_bundle(self._bundle()))
        digest = build_reflection(enriched)["source_snapshot_digest"]
        guard = GuardIo()
        with patch.object(persistence, "_io", guard):
            persisted = persistence.persist_learning_chain(state, enriched)
            persistence.resume_learning_chain(
                state, enriched["source_evaluation_id"], digest)
        self.assertIsNotNone(persisted["review_queue_request"])
        self.assertTrue(any(
            "review_queue" in Path(path).parts
            for _name, path in guard.calls if isinstance(path, str)
        ))
        for path, value in protected.items():
            self.assertEqual(path.read_bytes(), value)

    def test_transport_all_handles_use_agent_and_have_zero_governance_effect(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        from unittest.mock import patch

        import laos
        from agents.human_review import HumanReviewAgent

        agent_calls = []
        original_run = HumanReviewAgent.run

        def audited_run(agent, task, context):
            agent_calls.append((task["type"], context))
            return original_run(agent, task, context)

        blocked = (
            "build_application", "build_model_backend", "MemoryStore",
            "CandidateStore", "MemoryCore", "PolicyAgent",
            "LowRiskCandidateAgent", "LoopCoordinatorAgent",
            "ConversationReviewService", "ContextAgent", "ReviewGate",
        )
        with contextlib.ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch.object(
                    laos, name,
                    side_effect=AssertionError(
                        "review transport constructed " + name),
                ))
                for name in blocked
            }
            stack.enter_context(patch.object(
                HumanReviewAgent, "run", audited_run))
            for handle in ("review.list", "review.show", "review.decide"):
                with self.subTest(handle=handle):
                    state, request = self._published_chain()
                    root = Path(self._state_dir())
                    rq_id = request["review_queue_item_id"]
                    protected = {
                        str(path.relative_to(state)): path.read_bytes()
                        for namespace in ("evaluations", "review_queue")
                        for path in (Path(state) / namespace).rglob("*")
                        if path.is_file()
                    }
                    if handle == "review.list":
                        values = {}
                    elif handle == "review.show":
                        values = {"rq_id": rq_id}
                    else:
                        values = {
                            "rq_id": rq_id,
                            "decision_seq": 1,
                            "action": "accept",
                            "operator_claim": "operator",
                            "reason": "evidence sufficient",
                        }
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with contextlib.redirect_stdout(stdout), \
                            contextlib.redirect_stderr(stderr):
                        code = laos.main([
                            "--root", str(root),
                            "--state-dir", state,
                            "--task-json", json.dumps({
                                "type": handle, "input": values,
                            }, separators=(",", ":")),
                        ])
                    self.assertEqual(code, 0, stderr.getvalue())
                    self.assertNotEqual(stdout.getvalue(), "")
                    after = {
                        str(path.relative_to(state)): path.read_bytes()
                        for namespace in ("evaluations", "review_queue")
                        for path in (Path(state) / namespace).rglob("*")
                        if path.is_file()
                    }
                    self.assertEqual(after, protected)
                    self.assertEqual(self._tree_bytes(root), {})
                    if handle == "review.decide":
                        self.assertEqual(
                            len(list((Path(state) / "decisions").glob(
                                "*.json"))),
                            1,
                        )
                    else:
                        self.assertFalse(
                            (Path(state) / "decisions").exists())
            for mock in mocks.values():
                mock.assert_not_called()
        self.assertEqual(agent_calls, [
            ("review.list", {}),
            ("review.show", {}),
            ("review.decide", {}),
        ])

    def test_invalid_state_dir_cli_matrix_is_closed_and_memory_free(self):
        import contextlib
        import io
        import json
        import os
        from pathlib import Path
        from unittest.mock import patch

        import laos
        import human_review.review_store as review_store

        def vector(label):
            base = Path(self._state_dir())
            root = base / "root"
            root.mkdir()
            sync_roots = ()
            fake_home = None
            if label == "missing":
                state = base / "missing"
            elif label == "file":
                state = base / "state-file"
                state.write_bytes(b"x")
            elif label == "symlink":
                target = base / "target"
                target.mkdir()
                state = base / "state-link"
                os.symlink(target, state)
            elif label == "equal-root":
                state = root
            elif label == "state-in-root":
                state = root / "state"
                state.mkdir()
            elif label == "root-in-state":
                state = base / "state"
                state.mkdir()
                root = state / "root"
                root.mkdir()
            elif label == "repository-root":
                state = REPO_ROOT
            elif label == "known-sync-root":
                state = base / "sync-root"
                state.mkdir()
                sync_roots = (state,)
            else:
                fake_home = base / "home"
                state = fake_home / "Library" / "CloudStorage"
                state.mkdir(parents=True)
            return root, state, sync_roots, fake_home

        tasks = {
            "review.list": {},
            "review.show": {"rq_id": RQ_ID},
            "review.decide": {
                "rq_id": RQ_ID,
                "decision_seq": 1,
                "action": "accept",
                "operator_claim": "operator",
                "reason": "evidence sufficient",
            },
        }
        labels = (
            "missing", "file", "symlink", "equal-root",
            "state-in-root", "root-in-state", "repository-root",
            "known-sync-root", "cloud-storage-root",
        )
        real_expanduser = review_store.os.path.expanduser
        for label in labels:
            root, state, sync_roots, fake_home = vector(label)
            for handle, values in tasks.items():
                with self.subTest(label=label, handle=handle):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(patch.object(
                            review_store, "known_sync_roots",
                            return_value=sync_roots,
                        ))
                        if fake_home is not None:
                            stack.enter_context(patch.object(
                                review_store.os.path,
                                "expanduser",
                                side_effect=lambda path, home=fake_home: (
                                    str(home) if path == "~"
                                    else real_expanduser(path)
                                ),
                            ))
                        blocked = {
                            name: stack.enter_context(patch.object(
                                laos, name,
                                side_effect=AssertionError(
                                    "invalid state reached " + name),
                            ))
                            for name in (
                                "build_application", "build_model_backend",
                                "MemoryStore", "CandidateStore",
                            )
                        }
                        with contextlib.redirect_stdout(stdout), \
                                contextlib.redirect_stderr(stderr):
                            code = laos.main([
                                "--root", str(root),
                                "--state-dir", str(state),
                                "--task-json", json.dumps({
                                    "type": handle, "input": values,
                                }, separators=(",", ":")),
                            ])
                    self.assertEqual(code, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(json.loads(stderr.getvalue()), {
                        "error": {
                            "category": "precondition",
                            "code": "invalid_state_dir",
                        },
                    })
                    for mock in blocked.values():
                        mock.assert_not_called()
