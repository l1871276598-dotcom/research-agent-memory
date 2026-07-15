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


if __name__ == "__main__":
    unittest.main()
