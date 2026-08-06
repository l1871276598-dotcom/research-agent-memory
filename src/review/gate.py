import argparse
import importlib
from pathlib import Path

from .authority import AuthorityError, AuthorityStore, DECISION_ACTIONS, ReviewerProfile


REVIEW_ACTIONS = DECISION_ACTIONS
_REVIEW_AUTHORITY = object()


def _canonical_backend_and_authority():
    backend = importlib.import_module("memory_distill")
    gate = importlib.import_module("review.gate")
    return backend, gate._REVIEW_AUTHORITY


class ReviewGate:
    """Two-phase review gate.

    ``review`` publishes an immutable decision and never changes memory state.
    ``activate`` is the only public transition path and consumes only a
    decision id plus the expected active-set generation.
    """

    def __init__(
        self,
        root,
        state_dir=None,
        backend=None,
        authority_store=None,
        reviewer_profile=None,
    ):
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
        if authority_store is None:
            authority_store = AuthorityStore(self.root, self.state_dir)
        if not isinstance(authority_store, AuthorityStore):
            raise ValueError("authority_store is invalid")
        self.authority_store = authority_store
        if reviewer_profile is None:
            try:
                from profiles.store import current_profile

                active_profile = current_profile()
            except (ImportError, ValueError):
                active_profile = None
            if active_profile is None:
                reviewer_profile = ReviewerProfile("personal", "personal")
            else:
                reviewer_profile = ReviewerProfile(
                    active_profile.get("workspace"),
                    active_profile.get("confidentiality"),
                )
        if not isinstance(reviewer_profile, ReviewerProfile):
            raise ValueError("reviewer_profile must be an immutable ReviewerProfile")
        self.reviewer_profile = reviewer_profile

    def review(self, action, candidate_id, reason=None, workspace=None):
        """Publish a decision bound to the current exact candidate snapshot."""
        preflight = getattr(self.backend, "_preflight_candidate_impl", None)

        if workspace is not None:
            if workspace not in {"personal", "work"}:
                raise AuthorityError("invalid_workspace", "reviewer workspace is invalid")
            if workspace != self.reviewer_profile.workspace:
                raise AuthorityError("candidate_not_found", "candidate not found")

        def validate(record, snapshot):
            if callable(preflight):
                preflight(self.root, record, action, self.state_dir)

        decision = self.authority_store.publish_decision(
            action,
            candidate_id,
            reason=reason,
            workspace=self.reviewer_profile.workspace,
            confidentiality=self.reviewer_profile.confidentiality,
            validator=validate,
        )
        snapshot = decision["candidate_snapshot"]
        return {
            "status": "decided",
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
            "candidate_id": snapshot["candidate_id"],
            "candidate_artifact_sha256": snapshot["artifact_sha256"],
            "action": decision["action"],
            "expected_active_generation": decision["expected_active_generation"],
        }

    def activate(self, decision_id, expected_active_generation):
        """Apply one immutable decision through the generation CAS boundary."""

        def apply(decision):
            candidate_id = decision["candidate_snapshot"]["candidate_id"]
            action = decision["action"]
            if action == "reject":
                return self.backend._reject_candidate_impl(
                    argparse.Namespace(
                        root=str(self.root),
                        state_dir=str(self.state_dir),
                        id=candidate_id,
                        reason=decision["reason"],
                    ),
                    authority=self._authority,
                )
            if action == "conflict":
                return self.backend._conflict_candidate_impl(
                    argparse.Namespace(
                        root=str(self.root),
                        state_dir=str(self.state_dir),
                        id=candidate_id,
                    ),
                    authority=self._authority,
                )
            return self.backend._accept_candidate_impl(
                argparse.Namespace(
                    root=str(self.root),
                    state_dir=str(self.state_dir),
                    id=candidate_id,
                ),
                authority=self._authority,
            )

        receipt, backend_result = self.authority_store.activate(
            decision_id,
            expected_active_generation,
            apply,
        )
        return {
            "status": receipt["status"],
            "activation_id": receipt["activation_id"],
            "activation_sha256": receipt["activation_sha256"],
            "decision_id": receipt["decision_id"],
            "candidate_id": receipt["candidate_id"],
            "action": receipt["action"],
            "previous_generation": receipt["previous_generation"],
            "active_generation": receipt["active_generation"],
            "authorized_records": receipt["authorized_records"],
            "backend_status": backend_result.get("status")
            if isinstance(backend_result, dict)
            else None,
        }
