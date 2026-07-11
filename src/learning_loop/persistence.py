"""S5.4 persistence layer for the Phase 5 learning chain (baseline v4.3.1 §9).

Publishes the chain E -> R -> El -> (eligible only) Q under a caller-provided
``state_dir`` with typed zero-write preflight, a no-replace atomic publish
sequence, and crash-replay convergence. Queue-request publication is last so
no human-visible request exists before its upstream chain is durable.

Interface preconditions (documented contract, not enforced at runtime):
- one ``state_dir`` == one isolation domain (workspace/scope by caller, G-5);
- at most ONE writer per ``state_dir`` at a time (single-writer, caller
  guaranteed). Path-safety guarantees assume no external mutation of the
  directory tree while an entry point runs; malicious concurrent directory
  replacement (TOCTOU) is an explicit non-goal for this sliver.

All filesystem access goes through the single audited I/O shim ``_io``.
Reads outside the state_dir subtree are limited to the path-gate allowlist
(state_dir ancestors, sync roots, CloudStorage, forbidden_roots, repository
root candidates). All mutations are unconditionally confined to state_dir.
"""

import hashlib
import json
import os
import re
import stat

try:
    from src.learning_loop.reflection_builder import build_reflection, ReflectionInputError
    from src.learning_loop.eligibility import build_eligibility, EligibilityInputError
    from src.learning_loop.queue_request import build_review_queue_request, QueueRequestInputError
    from src.platform_paths import known_sync_roots
except ImportError:  # pragma: no cover - direct src/ imports
    from learning_loop.reflection_builder import build_reflection, ReflectionInputError
    from learning_loop.eligibility import build_eligibility, EligibilityInputError
    from learning_loop.queue_request import build_review_queue_request, QueueRequestInputError
    from platform_paths import known_sync_roots

_EVAL_ID_RE = re.compile(r"^eval_[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^snap_[0-9a-f]{64}$")

_E_NAME = "enriched_utility_evaluation.json"
_R_NAME = "reflection.json"
_EL_NAME = "eligibility.json"
_ALLOWED_DIGEST_ENTRIES = (_E_NAME, _R_NAME, _EL_NAME)


class PersistenceError(ValueError):
    """Single public error type for both entry points.

    Attributes:
        gate: str — "path", "preflight", "capability", "publish", "readback"
        code: str — stable machine-readable reason within the gate
    """

    def __init__(self, gate, code="", message=""):
        self.gate = gate
        self.code = code
        super().__init__(f"gate:{gate}" + (f":{code}" if code else "") + (f" {message}".rstrip() if message else ""))


class _Io:
    """The single audited I/O shim — the ONLY filesystem access surface."""

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
        while len(view):
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


_io = _Io()


def _canonical_json(value):
    """Private Appendix C canonicalizer — sole authority for on-disk bytes."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def _nonce():
    return os.urandom(8).hex()


def _lstat_or_none(path):
    try:
        return _io.lstat(path)
    except FileNotFoundError:
        return None


def _contains(root, path):
    return path == root or path.startswith(root + os.sep)


def _repository_root():
    """Lexical derivation from the frozen package layout src/learning_loop/."""
    module_path = _io.resolve_path(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(module_path)))


def _path_gate(state_dir, forbidden_roots):
    st = _lstat_or_none(state_dir)
    if st is None:
        raise PersistenceError("path", "missing_state_dir", state_dir)
    if stat.S_ISLNK(st.st_mode):
        raise PersistenceError("path", "state_dir_symlink", state_dir)
    if not stat.S_ISDIR(st.st_mode):
        raise PersistenceError("path", "state_dir_not_dir", state_dir)

    resolved = _io.resolve_path(state_dir)
    rejected = [_repository_root()]
    for root in known_sync_roots():
        rejected.append(_io.resolve_path(str(root)))
    rejected.append(_io.resolve_path(
        os.path.join(os.path.expanduser("~"), "Library", "CloudStorage")
    ))
    for root in forbidden_roots:
        rejected.append(_io.resolve_path(str(root)))
    for root in rejected:
        if _contains(root, resolved):
            raise PersistenceError("path", "forbidden_root", resolved)
    return resolved


def _require_not_symlink(path):
    st = _lstat_or_none(path)
    if st is not None and stat.S_ISLNK(st.st_mode):
        raise PersistenceError("path", "symlink_component", str(path))
    return st


def _cleanup_temps(directory):
    """A4-scoped cleanup: names are used ONLY for .tmp.* prefix match + unlink."""
    if _lstat_or_none(directory) is None:
        return
    for name in _io.scandir_names(directory):
        if name.startswith(".tmp."):
            _io.unlink(os.path.join(directory, name))


def _expected_chain(enriched):
    """Run the frozen producer chain in memory; wrap producer errors."""
    try:
        reflection = build_reflection(enriched)
        eligibility = build_eligibility(reflection, enriched)
        rq = None
        if eligibility["eligibility_status"] == "eligible":
            rq = build_review_queue_request(eligibility, reflection, enriched)
        return reflection, eligibility, rq
    except (ReflectionInputError, EligibilityInputError, QueueRequestInputError,
            TypeError, KeyError, ValueError) as exc:
        raise PersistenceError("preflight", "invalid_input", str(exc)) from None


def _rq_path_for(state_root, eligibility_id):
    payload = _canonical_json({"source_eligibility_id": eligibility_id})
    rq_id = "rq_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return os.path.join(state_root, "review_queue", rq_id + ".json")


class _Plan:
    """Resolved layout + expected bytes for one chain publication."""

    def __init__(self, state_root, enriched):
        reflection, eligibility, rq = _expected_chain(enriched)
        self.enriched = enriched
        self.reflection = reflection
        self.eligibility = eligibility
        self.rq = rq
        self.eval_id = enriched["source_evaluation_id"]
        self.digest = reflection["source_snapshot_digest"]
        self.state_root = state_root
        self.evaluations_dir = os.path.join(state_root, "evaluations")
        self.eval_dir = os.path.join(self.evaluations_dir, self.eval_id)
        self.digest_dir = os.path.join(self.eval_dir, self.digest)
        self.queue_dir = os.path.join(state_root, "review_queue")
        self.rq_path = _rq_path_for(state_root, eligibility["eligibility_id"])
        self.artifacts = [
            (os.path.join(self.digest_dir, _E_NAME),
             _canonical_json(enriched).encode("utf-8")),
            (os.path.join(self.digest_dir, _R_NAME),
             _canonical_json(reflection).encode("utf-8")),
            (os.path.join(self.digest_dir, _EL_NAME),
             _canonical_json(eligibility).encode("utf-8")),
        ]
        if rq is not None:
            self.artifacts.append(
                (self.rq_path, _canonical_json(rq).encode("utf-8"))
            )


def _preflight(plan):
    """Typed zero-write preflight: temp cleanup, prefix legality, byte checks."""
    for directory in (plan.evaluations_dir, plan.eval_dir, plan.digest_dir,
                      plan.queue_dir):
        _require_not_symlink(directory)
    _cleanup_temps(plan.state_root)
    _cleanup_temps(plan.digest_dir)
    _cleanup_temps(plan.queue_dir)

    if _lstat_or_none(plan.digest_dir) is not None:
        for name in _io.scandir_names(plan.digest_dir):
            if name not in _ALLOWED_DIGEST_ENTRIES:
                raise PersistenceError("preflight", "unexpected_entry", name)

    presence = []
    for target, expected in plan.artifacts:
        st = _require_not_symlink(target)
        presence.append(st is not None)
    rq_present = presence[3] if len(presence) == 4 else False
    if plan.rq is None:
        st = _require_not_symlink(plan.rq_path)
        if st is not None:
            raise PersistenceError("preflight", "corruption",
                                   "queue request present on ineligible chain")

    upstream_present = True
    for target, exists in zip((t for t, _ in plan.artifacts), presence):
        if exists and not upstream_present:
            raise PersistenceError("preflight", "non_prefix", str(target))
        upstream_present = exists if not upstream_present else exists or False
    # strict monotone prefix: no artifact may exist unless all before it exist
    seen_missing = False
    for (target, _), exists in zip(plan.artifacts, presence):
        if not exists:
            seen_missing = True
        elif seen_missing:
            raise PersistenceError("preflight", "non_prefix", str(target))

    for (target, expected), exists in zip(plan.artifacts, presence):
        if exists and _io.read_bytes(target) != expected:
            raise PersistenceError("preflight", "byte_mismatch", str(target))
    return presence


def _capability_probe(state_root):
    source = os.path.join(state_root, ".tmp.probe." + _nonce())
    target = os.path.join(state_root, ".tmp.probe-target." + _nonce())
    try:
        fd = _io.open_exclusive(source)
        _io.write_all(fd, b"probe")
        _io.fsync_file(fd)
        _io.close(fd)
        _io.link(source, target)
        _io.unlink(target)
        _io.unlink(source)
        _io.fsync_dir(state_root)
    except OSError as exc:
        raise PersistenceError("capability", "probe_failed", str(exc)) from None


def _ensure_dir_chain(levels):
    """mkdir missing levels root-to-leaf; (re-)fsync each parent regardless."""
    for parent, child in levels:
        if _lstat_or_none(child) is None:
            _io.mkdir(child)
        _io.fsync_dir(parent)


def _publish_file(target, expected):
    directory = os.path.dirname(target)
    temp = os.path.join(directory,
                        ".tmp." + os.path.basename(target) + "." + _nonce())
    try:
        fd = _io.open_exclusive(temp)
        _io.write_all(fd, expected)
        _io.fsync_file(fd)
        _io.close(fd)
    except OSError as exc:
        raise PersistenceError("publish", "temp_write_failed", str(exc)) from None
    if _io.read_bytes(temp) != expected:
        raise PersistenceError("readback", "temp_mismatch", str(target))
    try:
        _io.link(temp, target)
    except FileExistsError:
        if _io.read_bytes(target) != expected:
            raise PersistenceError("publish", "conflict", str(target)) from None
    except OSError as exc:
        raise PersistenceError("publish", "link_failed", str(exc)) from None
    _io.unlink(temp)
    _io.fsync_dir(directory)


def _run_chain(plan):
    presence = _preflight(plan)
    _capability_probe(plan.state_root)

    _ensure_dir_chain((
        (plan.state_root, plan.evaluations_dir),
        (plan.evaluations_dir, plan.eval_dir),
        (plan.eval_dir, plan.digest_dir),
    ))

    for index, ((target, expected), existed) in enumerate(
            zip(plan.artifacts, presence)):
        is_queue = target == plan.rq_path
        if is_queue:
            _ensure_dir_chain(((plan.state_root, plan.queue_dir),))
        if existed:
            _io.fsync_dir(os.path.dirname(target))
        else:
            _publish_file(target, expected)

    if plan.rq is None and _lstat_or_none(plan.queue_dir) is not None:
        _io.fsync_dir(plan.queue_dir)

    return {
        "eligibility": json.loads(_canonical_json(plan.eligibility)),
        "review_queue_request": (
            None if plan.rq is None else json.loads(_canonical_json(plan.rq))
        ),
    }


def persist_learning_chain(state_dir, enriched, *, forbidden_roots=()):
    """First write (and same-object retry) of a learning chain."""
    state_root = _path_gate(state_dir, forbidden_roots)
    plan = _Plan(state_root, enriched)
    return _run_chain(plan)


def resume_learning_chain(state_dir, evaluation_id, source_snapshot_digest, *,
                          forbidden_roots=()):
    """State-rooted recovery. Precondition: E is durably on disk."""
    if not isinstance(evaluation_id, str) or not _EVAL_ID_RE.match(evaluation_id):
        raise PersistenceError("path", "bad_evaluation_id", str(evaluation_id))
    if not isinstance(source_snapshot_digest, str) \
            or not _DIGEST_RE.match(source_snapshot_digest):
        raise PersistenceError("path", "bad_digest", str(source_snapshot_digest))

    state_root = _path_gate(state_dir, forbidden_roots)
    digest_dir = os.path.join(state_root, "evaluations", evaluation_id,
                              source_snapshot_digest)
    e_path = os.path.join(digest_dir, _E_NAME)
    for component in (os.path.join(state_root, "evaluations"),
                      os.path.dirname(digest_dir), digest_dir, e_path):
        _require_not_symlink(component)
    if _lstat_or_none(e_path) is None:
        for name in (_R_NAME, _EL_NAME):
            if _lstat_or_none(os.path.join(digest_dir, name)) is not None:
                raise PersistenceError("preflight", "non_prefix",
                                       "downstream without root evidence E")
        raise PersistenceError("preflight", "missing_e", e_path)

    raw = _io.read_bytes(e_path)
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise PersistenceError("preflight", "invalid_input", str(exc)) from None
    if raw.decode("utf-8", errors="replace") != _canonical_json(parsed):
        raise PersistenceError("preflight", "noncanonical_e", e_path)
    if not isinstance(parsed, dict) \
            or parsed.get("source_evaluation_id") != evaluation_id \
            or not isinstance(parsed.get("source_evaluation_snapshot"), dict) \
            or parsed["source_evaluation_snapshot"].get("evaluation_id") != evaluation_id:
        raise PersistenceError("preflight", "path_binding", e_path)
    recomputed = "snap_" + hashlib.sha256(
        _canonical_json(parsed["source_evaluation_snapshot"]).encode("utf-8")
    ).hexdigest()
    if recomputed != source_snapshot_digest:
        raise PersistenceError("preflight", "digest_mismatch", e_path)

    plan = _Plan(state_root, parsed)
    if plan.digest != source_snapshot_digest or plan.eval_id != evaluation_id:
        raise PersistenceError("preflight", "path_binding", e_path)
    return _run_chain(plan)
