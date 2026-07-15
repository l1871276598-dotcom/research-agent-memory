"""Human Review HR.2 write store.

All filesystem access is confined to the audited ``_Io`` shim.  The module
depends on the frozen queue builder only through constructor injection.
"""

from datetime import datetime, timedelta, timezone
import json
import os
import re
import stat

from human_review.decision import (
    ACTIONS,
    ERROR_CATEGORIES,
    DecisionSchemaError,
    build_decision,
    canonical_bytes,
    command_projection,
    decode_event,
    reduce_state,
)
from platform_paths import known_sync_roots


_RQ_ID = re.compile(r"^rq_[0-9a-f]{64}$")
_EVAL_ID = re.compile(r"^eval_[0-9a-f]{64}$")
_SNAPSHOT_DIGEST = re.compile(r"^snap_[0-9a-f]{64}$")
_ELIGIBILITY_ID = re.compile(r"^elig_[0-9a-f]{64}$")
_DECISION_NAME = re.compile(
    r"^(rq_[0-9a-f]{64})\.decision_([12])\.json$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_RQ_KEYS = frozenset({
    "schema_version", "template_version", "review_queue_item_id",
    "source_eligibility_id", "source_evaluation_id",
    "source_snapshot_digest", "experiment", "status", "summary",
    "evidence_summary", "missing_information", "decision_options",
})
_EXPERIMENT_KEYS = frozenset({
    "task_id", "without_memory_run_id", "with_memory_run_id",
})
_EVIDENCE_SUMMARY_KEYS = frozenset({
    "validation_verdict", "pack_utility_delta", "verified", "unknown",
    "contradicted",
})
_MISSING_INFORMATION_KEYS = frozenset({
    "claim_id", "statement", "evidence_ref",
})


class ReviewError(Exception):
    """Stable internal Human Review error with frozen machine fields."""

    def __init__(self, code, *, rq_id=None, decision_seq=None):
        self.category = ERROR_CATEGORIES[code]
        self.code = code
        self.rq_id = rq_id
        self.decision_seq = decision_seq
        super().__init__(f"{self.category}/{self.code}")


class _Io:
    """The only filesystem access surface for Human Review."""

    def lstat(self, path):
        return os.lstat(path)

    def scandir_names(self, path):
        return sorted(os.listdir(path))

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def open_exclusive(self, path):
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)

    def write_all(self, fd, data):
        view = memoryview(bytes(data))
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write not progressing")
            view = view[written:]

    def close(self, fd):
        os.close(fd)

    def fsync_file(self, fd):
        os.fsync(fd)

    def fsync_dir(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def mkdir(self, path):
        os.mkdir(path)

    def link(self, source, target):
        os.link(source, target)

    def unlink(self, path):
        os.unlink(path)

    def resolve_path(self, path):
        return os.path.realpath(path)

    def path_limit(self, path):
        probe = path
        while True:
            try:
                return os.pathconf(probe, "PC_PATH_MAX")
            except (FileNotFoundError, NotADirectoryError):
                parent = os.path.dirname(probe)
                if parent == probe:
                    raise
                probe = parent


_io = _Io()


def _default_nonce():
    return os.urandom(16).hex()


def _default_clock():
    return datetime.now(timezone.utc)


def _contains(root, path):
    return path == root or path.startswith(root + os.sep)


def _raise(code, rq_id=None, decision_seq=None):
    raise ReviewError(code, rq_id=rq_id, decision_seq=decision_seq)


def _lstat_or_none(io, path, code, rq_id=None, decision_seq=None):
    try:
        return io.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _raise(code, rq_id, decision_seq)


def _repository_root(io):
    module = io.resolve_path(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(module)))


def _path_gate(state_dir, forbidden_roots, io):
    try:
        st = io.lstat(state_dir)
    except OSError:
        _raise("invalid_state_dir")
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _raise("invalid_state_dir")

    try:
        root = io.resolve_path(state_dir)
        rejected = [io.resolve_path(_repository_root(io))]
        rejected.extend(io.resolve_path(str(path)) for path in known_sync_roots())
        rejected.append(io.resolve_path(os.path.join(
            os.path.expanduser("~"), "Library", "CloudStorage")))
        rejected.extend(io.resolve_path(str(path)) for path in forbidden_roots)
    except (OSError, TypeError, ValueError):
        _raise("invalid_state_dir")

    for forbidden in rejected:
        if _contains(forbidden, root) or _contains(root, forbidden):
            _raise("invalid_state_dir")
    return root


def _check_path_length(path, io, rq_id=None, decision_seq=None):
    try:
        limit = io.path_limit(path)
    except OSError:
        _raise("path_read_failed", rq_id, decision_seq)
    if type(limit) is not int or limit <= 0:
        _raise("path_read_failed", rq_id, decision_seq)
    if len(os.fsencode(path)) > limit:
        _raise("path_too_long", rq_id, decision_seq)


def _decode_canonical_object(raw):
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("invalid canonical bytes")

    def reject_duplicates(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = child
        return value

    def reject_constant(value):
        raise ValueError(f"illegal constant {value}")

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )
    if type(value) is not dict or canonical_bytes(value) != raw:
        raise ValueError("non-canonical object")
    return value


def _validate_local_request(raw, rq_id, decision_seq=None):
    try:
        request = _decode_canonical_object(raw)
        if set(request) != set(_RQ_KEYS):
            raise ValueError("request key set")
        if type(request["schema_version"]) is not int \
                or request["schema_version"] != 1:
            raise ValueError("schema_version")
        if type(request["template_version"]) is not int \
                or request["template_version"] != 1:
            raise ValueError("template_version")
        if type(request["review_queue_item_id"]) is not str \
                or _RQ_ID.fullmatch(request["review_queue_item_id"]) is None \
                or request["review_queue_item_id"] != rq_id:
            raise ValueError("request identity")
        if type(request["source_evaluation_id"]) is not str \
                or _EVAL_ID.fullmatch(request["source_evaluation_id"]) is None:
            raise ValueError("evaluation identity")
        if type(request["source_snapshot_digest"]) is not str \
                or _SNAPSHOT_DIGEST.fullmatch(
                    request["source_snapshot_digest"]) is None:
            raise ValueError("snapshot identity")
        if type(request["source_eligibility_id"]) is not str \
                or _ELIGIBILITY_ID.fullmatch(
                    request["source_eligibility_id"]) is None:
            raise ValueError("eligibility identity")
        if request["status"] != "pending" \
                or request["decision_options"] != [
                    "accept", "reject", "defer", "revise"]:
            raise ValueError("request constants")
        experiment = request["experiment"]
        if type(experiment) is not dict \
                or set(experiment) != set(_EXPERIMENT_KEYS) \
                or any(type(value) is not str for value in experiment.values()):
            raise ValueError("request experiment")
        evidence = request["evidence_summary"]
        if type(evidence) is not dict \
                or set(evidence) != set(_EVIDENCE_SUMMARY_KEYS) \
                or evidence["validation_verdict"] not in ("A", "B") \
                or type(evidence["pack_utility_delta"]) not in (int, float):
            raise ValueError("request evidence summary")
        for field in ("verified", "unknown", "contradicted"):
            if type(evidence[field]) is not int or evidence[field] < 0:
                raise ValueError("request evidence count")
        missing = request["missing_information"]
        if type(missing) is not list or any(
                type(item) is not dict
                or set(item) != set(_MISSING_INFORMATION_KEYS)
                or any(type(value) is not str for value in item.values())
                for item in missing):
            raise ValueError("request missing information")
        if type(request["summary"]) is not str:
            raise ValueError("request field types")
    except Exception:
        _raise("malformed_request", rq_id, decision_seq)
    return request


def _require_directory(io, path, missing_code, rq_id, decision_seq=None):
    st = _lstat_or_none(
        io, path, "path_read_failed", rq_id, decision_seq)
    if st is None:
        _raise(missing_code, rq_id, decision_seq)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _raise("unsafe_path", rq_id, decision_seq)


def _read_chain_object(io, path, rq_id, decision_seq=None):
    st = _lstat_or_none(
        io, path, "path_read_failed", rq_id, decision_seq)
    if st is None or stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _raise("invalid_review_chain", rq_id, decision_seq)
    try:
        raw = io.read_bytes(path)
    except OSError:
        _raise("path_read_failed", rq_id, decision_seq)
    try:
        return _decode_canonical_object(raw)
    except Exception:
        _raise("invalid_review_chain", rq_id, decision_seq)


def _validate_frozen_review_chain(root, rq_id, rq_builder, io,
                                  decision_seq=None):
    queue = os.path.join(root, "review_queue")
    _check_path_length(queue, io, rq_id, decision_seq)
    queue_st = _lstat_or_none(
        io, queue, "path_read_failed", rq_id, decision_seq)
    if queue_st is None:
        _raise("request_not_found", rq_id, decision_seq)
    if stat.S_ISLNK(queue_st.st_mode) or not stat.S_ISDIR(queue_st.st_mode):
        _raise("malformed_review_queue_namespace", rq_id, decision_seq)

    rq_path = os.path.join(queue, rq_id + ".json")
    _check_path_length(rq_path, io, rq_id, decision_seq)
    rq_st = _lstat_or_none(
        io, rq_path, "path_read_failed", rq_id, decision_seq)
    if rq_st is None:
        _raise("request_not_found", rq_id, decision_seq)
    if stat.S_ISLNK(rq_st.st_mode) or not stat.S_ISREG(rq_st.st_mode):
        _raise("malformed_directory_entry", rq_id)
    try:
        rq_raw = io.read_bytes(rq_path)
    except OSError:
        _raise("path_read_failed", rq_id, decision_seq)
    request = _validate_local_request(rq_raw, rq_id, decision_seq)

    evaluations = os.path.join(root, "evaluations")
    evaluation = os.path.join(evaluations, request["source_evaluation_id"])
    digest = os.path.join(evaluation, request["source_snapshot_digest"])
    for path in (evaluations, evaluation, digest):
        _check_path_length(path, io, rq_id, decision_seq)
        _require_directory(
            io, path, "invalid_review_chain", rq_id, decision_seq)

    paths = [
        os.path.join(digest, "enriched_utility_evaluation.json"),
        os.path.join(digest, "reflection.json"),
        os.path.join(digest, "eligibility.json"),
    ]
    for path in paths:
        _check_path_length(path, io, rq_id, decision_seq)
    enriched, reflection, eligibility = (
        _read_chain_object(io, path, rq_id, decision_seq) for path in paths
    )
    try:
        if enriched["source_evaluation_id"] \
                != request["source_evaluation_id"] \
                or enriched["source_evaluation_snapshot"]["evaluation_id"] \
                != request["source_evaluation_id"] \
                or reflection["source_evaluation_id"] \
                != request["source_evaluation_id"] \
                or reflection["source_snapshot_digest"] \
                != request["source_snapshot_digest"] \
                or eligibility["eligibility_id"] \
                != request["source_eligibility_id"] \
                or eligibility["source_reflection_id"] \
                != reflection["reflection_id"]:
            raise ValueError("review-chain anchor mismatch")
    except Exception:
        _raise("invalid_review_chain", rq_id, decision_seq)
    try:
        expected = rq_builder(eligibility, reflection, enriched)
        expected_raw = canonical_bytes(expected)
    except Exception:
        _raise("invalid_review_chain", rq_id, decision_seq)
    if expected_raw != rq_raw:
        _raise("malformed_request", rq_id, decision_seq)
    return request


def _decision_namespace(root, io, rq_id, decision_seq):
    decisions = os.path.join(root, "decisions")
    _check_path_length(decisions, io, rq_id, decision_seq)
    st = _lstat_or_none(
        io, decisions, "path_read_failed", rq_id, decision_seq)
    if st is None:
        return decisions, False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        _raise("malformed_decisions_namespace")
    return decisions, True


def _raise_missing_request(root, io, rq_id, decision_seq):
    """Distinguish an absent request from an already-published orphan D."""
    decisions, exists = _decision_namespace(root, io, rq_id, decision_seq)
    if exists:
        _barrier(io, (decisions,), rq_id, decision_seq)
        events = _read_events(io, decisions, rq_id, decision_seq)
        if events:
            _state(events, rq_id, decision_seq)
            _raise("orphan_decision", rq_id)
    _raise("request_not_found", rq_id, decision_seq)


def _barrier(io, paths, rq_id, decision_seq):
    for path in paths:
        try:
            io.fsync_dir(path)
        except OSError:
            _raise("precommit_directory_fsync_failed", rq_id, decision_seq)


def _read_event(io, path, rq_id, seq):
    try:
        raw = io.read_bytes(path)
    except OSError:
        _raise("path_read_failed", rq_id, seq)
    try:
        return decode_event(raw, rq_id, seq)
    except DecisionSchemaError:
        _raise("malformed_decision", rq_id, seq)


def _read_events(io, decisions, rq_id, decision_seq):
    try:
        names = io.scandir_names(decisions)
    except OSError:
        _raise("path_read_failed", rq_id, decision_seq)

    slots = {}
    for name in names:
        if name.startswith(".tmp."):
            continue
        match = _DECISION_NAME.fullmatch(name)
        if match is None:
            _raise("malformed_directory_entry", rq_id)
        path = os.path.join(decisions, name)
        st = _lstat_or_none(
            io, path, "path_read_failed", rq_id, decision_seq)
        if st is None:
            _raise("path_read_failed", rq_id, decision_seq)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            _raise("malformed_directory_entry", rq_id)
        if match.group(1) == rq_id:
            slots[int(match.group(2))] = path

    events = {}
    for seq in (2, 1):
        if seq in slots:
            events[seq] = _read_event(io, slots[seq], rq_id, seq)
    return events


def _state(events, rq_id, decision_seq):
    try:
        return reduce_state(events.values())
    except DecisionSchemaError:
        bad_seq = 2 if 2 in events else decision_seq
        _raise("malformed_decision", rq_id, bad_seq)


def _compare_existing(io, decisions, event, command, rq_id, decision_seq):
    _barrier(io, (decisions,), rq_id, decision_seq)
    if command_projection(event) == command:
        return {"status": "idempotent", "event": event}
    _raise("decision_slot_conflict", rq_id, decision_seq)


def _compare_after_link_race(io, decisions, command, rq_id, decision_seq):
    events = _read_events(io, decisions, rq_id, decision_seq)
    _state(events, rq_id, decision_seq)
    if decision_seq not in events:
        _raise("path_read_failed", rq_id, decision_seq)
    return _compare_existing(
        io, decisions, events[decision_seq], command, rq_id, decision_seq)


def _recorded_at(clock_factory, rq_id, decision_seq):
    try:
        value = clock_factory()
        if type(value) is not datetime or value.tzinfo is None \
                or value.utcoffset() != timedelta(0):
            raise ValueError("clock must return an aware UTC datetime")
        return (
            f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
            f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}Z"
        )
    except Exception:
        _raise("clock_unavailable", rq_id, decision_seq)


def _bootstrap_decisions(io, root, decisions, rq_id, decision_seq):
    try:
        io.mkdir(decisions)
    except FileExistsError:
        try:
            st = io.lstat(decisions)
        except OSError:
            _raise("mkdir_failed")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            _raise("malformed_decisions_namespace")
    except OSError:
        _raise("mkdir_failed")
    _barrier(io, (root,), rq_id, decision_seq)


def _nonce(nonce_factory, rq_id, decision_seq):
    try:
        value = nonce_factory()
    except Exception:
        _raise("temp_write_failed", rq_id, decision_seq)
    if type(value) is not str or _NONCE.fullmatch(value) is None:
        _raise("temp_write_failed", rq_id, decision_seq)
    return value


def _write_temp(io, temp, expected, rq_id, decision_seq):
    try:
        fd = io.open_exclusive(temp)
    except OSError:
        _raise("temp_write_failed", rq_id, decision_seq)

    failure = None
    try:
        io.write_all(fd, expected)
        io.fsync_file(fd)
    except OSError as exc:
        failure = exc
    try:
        io.close(fd)
    except OSError as exc:
        if failure is None:
            failure = exc
    if failure is not None:
        _cleanup_own_temp(io, temp)
        _raise("temp_write_failed", rq_id, decision_seq)


def _cleanup_own_temp(io, temp):
    try:
        io.unlink(temp)
    except OSError:
        pass


def _publish(io, queue, decisions, final, event, nonce_factory,
             rq_id, decision_seq, command):
    expected = canonical_bytes(event)
    nonce = _nonce(nonce_factory, rq_id, decision_seq)
    temp = os.path.join(
        decisions, ".tmp." + os.path.basename(final) + "." + nonce)
    _check_path_length(temp, io, rq_id, decision_seq)

    _barrier(io, (queue,), rq_id, decision_seq)

    created = False
    try:
        _write_temp(io, temp, expected, rq_id, decision_seq)
        created = True
        try:
            temp_raw = io.read_bytes(temp)
        except FileNotFoundError:
            _raise("temp_missing_before_link", rq_id, decision_seq)
        except OSError:
            _raise("temp_read_failed", rq_id, decision_seq)
        if temp_raw != expected:
            _raise("temp_byte_mismatch", rq_id, decision_seq)

        try:
            io.link(temp, final)
        except FileExistsError:
            _cleanup_own_temp(io, temp)
            created = False
            return _compare_after_link_race(
                io, decisions, command, rq_id, decision_seq)
        except OSError:
            _raise("link_failed", rq_id, decision_seq)

        _cleanup_own_temp(io, temp)
        created = False
        try:
            io.fsync_dir(decisions)
        except OSError:
            _raise("decision_directory_fsync_failed", rq_id, decision_seq)

        try:
            final_raw = io.read_bytes(final)
        except FileNotFoundError:
            _raise("decision_final_missing", rq_id, decision_seq)
        except OSError:
            _raise("final_readback_failed", rq_id, decision_seq)
        if final_raw != expected:
            _raise("decision_final_byte_mismatch", rq_id, decision_seq)
        try:
            persisted = decode_event(final_raw, rq_id, decision_seq)
        except DecisionSchemaError:
            _raise("malformed_decision", rq_id, decision_seq)
        return {"status": "published", "event": persisted}
    finally:
        if created:
            _cleanup_own_temp(io, temp)


def validate_frozen_review_chain(state_dir, rq_id, *, rq_builder,
                                 forbidden_roots=(), io=None):
    """Pure-read G8 seam; returns the validated 12-key request."""
    if type(rq_id) is not str or _RQ_ID.fullmatch(rq_id) is None:
        _raise("invalid_review_queue_item_id")
    active_io = _io if io is None else io
    root = _path_gate(state_dir, forbidden_roots, active_io)
    return _validate_frozen_review_chain(root, rq_id, rq_builder, active_io)


class ReviewStore:
    """HR.2 decision writer; read/list transport remains deferred to HR.3."""

    def __init__(self, state_dir, forbidden_roots, rq_builder, io=None,
                 nonce_factory=None, clock_factory=None):
        self.state_dir = state_dir
        self.forbidden_roots = tuple(forbidden_roots)
        self.rq_builder = rq_builder
        self.io = _io if io is None else io
        self.nonce_factory = _default_nonce if nonce_factory is None \
            else nonce_factory
        self.clock_factory = _default_clock if clock_factory is None \
            else clock_factory

    def decide(self, rq_id, decision_seq, action, operator_claim, reason):
        if type(rq_id) is not str or _RQ_ID.fullmatch(rq_id) is None:
            _raise("invalid_review_queue_item_id")
        if type(decision_seq) is not int or decision_seq not in (1, 2):
            _raise("invalid_decision_seq", rq_id)
        if type(action) is not str or action not in ACTIONS:
            _raise("invalid_action", rq_id, decision_seq)
        if type(operator_claim) is not str or not operator_claim:
            _raise("invalid_operator_claim", rq_id, decision_seq)
        if type(reason) is not str or not reason:
            _raise("invalid_reason", rq_id, decision_seq)
        root = _path_gate(self.state_dir, self.forbidden_roots, self.io)
        decisions = os.path.join(root, "decisions")
        final = os.path.join(
            decisions, f"{rq_id}.decision_{decision_seq}.json")
        _check_path_length(final, self.io, rq_id, decision_seq)
        try:
            _validate_frozen_review_chain(
                root, rq_id, self.rq_builder, self.io, decision_seq)
        except ReviewError as exc:
            if exc.code == "request_not_found":
                _raise_missing_request(
                    root, self.io, rq_id, decision_seq)
            raise
        queue = os.path.join(root, "review_queue")
        decisions, exists = _decision_namespace(
            root, self.io, rq_id, decision_seq)
        _barrier(
            self.io,
            (queue, decisions) if exists else (queue,),
            rq_id,
            decision_seq,
        )
        events = _read_events(
            self.io, decisions, rq_id, decision_seq) if exists else {}
        command = (rq_id, decision_seq, action, operator_claim, reason)
        state = _state(events, rq_id, decision_seq)
        if decision_seq in events:
            return _compare_existing(
                self.io, decisions, events[decision_seq], command,
                rq_id, decision_seq)

        if decision_seq == 1:
            allowed = state == "pending"
        else:
            allowed = state == "deferred" and action != "defer"
        if not allowed:
            _raise("invalid_state_transition", rq_id, decision_seq)

        recorded_at = _recorded_at(
            self.clock_factory, rq_id, decision_seq)
        event = build_decision(
            rq_id, decision_seq, action, operator_claim, reason, recorded_at)
        _bootstrap_decisions(
            self.io, root, decisions, rq_id, decision_seq)
        return _publish(
            self.io,
            queue,
            decisions,
            final,
            event,
            self.nonce_factory,
            rq_id,
            decision_seq,
            command,
        )
