import argparse
import importlib
from pathlib import Path


REVIEW_ACTIONS = frozenset({"accept", "reject", "merge", "conflict"})
_WORKSPACES = frozenset({"personal", "work"})
_MERGE_REQUESTS = frozenset({"merge", "support"})
_ACCEPT_REQUESTS = frozenset(
    {"add", "create", "ADD", "UPDATE", "DEPRECATE", "supersede", "context_transition"}
)
_REVIEW_AUTHORITY = object()


def _canonical_backend_and_authority():
    backend = importlib.import_module("memory_distill")
    gate = importlib.import_module("review.gate")
    return backend, gate._REVIEW_AUTHORITY


class ReviewGate:
    def __init__(self, root, state_dir=None, backend=None):
        try:
            self.root = Path(root).expanduser()
        except TypeError as exc:
            raise ValueError("root must be a filesystem path") from exc
        self.state_dir = None if state_dir is None else Path(state_dir).expanduser()
        canonical_backend, authority = _canonical_backend_and_authority()
        if backend is None:
            backend = canonical_backend
        required = (
            "_accept_candidate_impl",
            "_candidate_record",
            "_conflict_candidate_impl",
            "_reject_candidate_impl",
        )
        if any(not callable(getattr(backend, name, None)) for name in required):
            raise ValueError("review backend is invalid")
        self.backend = backend
        self._authority = authority if backend is canonical_backend else _REVIEW_AUTHORITY

    def review(self, action, candidate_id, reason=None, workspace=None):
        if action not in REVIEW_ACTIONS:
            raise ValueError("unsupported review action")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("reason must be a non-empty string")
        if workspace is not None and workspace not in _WORKSPACES:
            raise ValueError("workspace must be personal or work")

        candidate_id = candidate_id.strip()
        candidate = None
        if workspace is not None or action != "reject":
            candidate = self.backend._candidate_record(self.root, candidate_id)

        if workspace is not None and candidate.get("workspace") != workspace:
            raise ValueError("candidate not found")

        if action == "reject":
            fields = {"root": str(self.root), "id": candidate_id, "reason": reason}
            if self.state_dir is not None:
                fields["state_dir"] = str(self.state_dir)
            return self.backend._reject_candidate_impl(
                argparse.Namespace(**fields), authority=self._authority
            )

        self._require_state_dir()
        requested = candidate.get("requested_action")
        candidate_action = candidate.get("candidate_action")
        if action == "conflict":
            if requested != "conflict" and candidate_action != "REVIEW_REQUIRED":
                raise ValueError("candidate does not request conflict review")
            return self.backend._conflict_candidate_impl(
                argparse.Namespace(root=str(self.root), state_dir=str(self.state_dir), id=candidate_id),
                authority=self._authority,
            )
        if action == "merge":
            if requested not in _MERGE_REQUESTS:
                raise ValueError("candidate does not request merge review")
        elif requested not in _ACCEPT_REQUESTS or candidate_action == "REVIEW_REQUIRED":
            raise ValueError("candidate requires an explicit review action")
        return self.backend._accept_candidate_impl(
            argparse.Namespace(root=str(self.root), state_dir=str(self.state_dir), id=candidate_id),
            authority=self._authority,
        )

    def _require_state_dir(self):
        if self.state_dir is None:
            raise ValueError("state_dir is required for this review action")
