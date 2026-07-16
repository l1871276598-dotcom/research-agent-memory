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
