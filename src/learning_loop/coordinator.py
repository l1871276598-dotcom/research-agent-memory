from __future__ import annotations

from .loop import (
    LearningLoop,
    _canonical,
    _digest_text,
    _read_json,
    _timestamp,
    _write_json,
    validate_task,
)


_STATES = (
    "task_created",
    "context_built",
    "execution_completed",
    "outcome_recorded",
    "reflection_created",
    "candidate_created",
    "awaiting_review",
    "accepted",
    "rejected",
    "memory_activated",
    "verification_scheduled",
    "verified",
    "verification_failed",
)
_ALLOWED = {
    "task_created": {"context_built"},
    "context_built": {"execution_completed"},
    "execution_completed": {"outcome_recorded"},
    "outcome_recorded": {"reflection_created"},
    "reflection_created": {"candidate_created"},
    "candidate_created": {"awaiting_review", "verified"},
    "awaiting_review": {"accepted", "rejected"},
    "accepted": {"memory_activated"},
    "rejected": set(),
    "memory_activated": {"verification_scheduled"},
    "verification_scheduled": {"verified", "verification_failed"},
    "verified": set(),
    "verification_failed": set(),
}
_ARTIFACT_STATES = (
    (("context.json",), "context_built"),
    (("result.md", "evidence.json"), "execution_completed"),
    (("outcome.json",), "outcome_recorded"),
    (("reflection.md",), "reflection_created"),
    (("policy_suggestions.md",), "candidate_created"),
)
_TERMINAL = {"rejected", "verified", "verification_failed"}


class AutoUpdateLoopCoordinator:
    """Advance the reviewed learning loop without making review decisions."""

    def __init__(self, loop: LearningLoop):
        if not isinstance(loop, LearningLoop):
            raise ValueError("loop must be a LearningLoop")
        self.loop = loop

    def _path(self, run_id):
        return self.loop._run_dir(run_id) / "auto_loop.json"

    def _load_or_create(self, task):
        validate_task(task)
        path = self._path(task["run_id"])
        task_digest = _digest_text(_canonical(task))
        if path.is_file():
            state = _read_json(path)
            if state.get("task_digest") != task_digest:
                raise ValueError("auto-loop run_id already belongs to a different task")
            return state
        now = _timestamp()
        state = {
            "schema_version": 1,
            "run_id": task["run_id"],
            "task_id": task["task_id"],
            "task_digest": task_digest,
            "state": "task_created",
            "state_history": [{"state": "task_created", "at": now, "source": "task"}],
            "created_at": now,
            "updated_at": now,
            "last_error": None,
            "error_history": [],
        }
        _write_json(path, state)
        return state

    def _save(self, state, **updates):
        state.update(updates)
        state["updated_at"] = _timestamp()
        _write_json(self._path(state["run_id"]), state)
        return state

    def _transition(self, state, target, source, **updates):
        current = state["state"]
        if current == target:
            return self._save(state, **updates) if updates else state
        if target not in _ALLOWED.get(current, set()):
            raise ValueError(f"invalid auto-loop transition: {current} -> {target}")
        state["state"] = target
        state.setdefault("state_history", []).append(
            {"state": target, "at": _timestamp(), "source": source}
        )
        return self._save(state, **updates)

    def _record_error(self, state, exc):
        history = list(state.get("error_history") or [])
        history.append({"at": _timestamp(), "message": str(exc)})
        self._save(state, last_error=str(exc), error_history=history)

    def _reconcile_artifacts(self, state, run_status):
        artifacts = run_status.get("artifacts") or {}
        for names, target in _ARTIFACT_STATES:
            if not all(name in artifacts for name in names):
                break
            if state["state"] not in {
                "task_created",
                "context_built",
                "execution_completed",
                "outcome_recorded",
                "reflection_created",
                "candidate_created",
            }:
                break
            if _STATES.index(state["state"]) < _STATES.index(target):
                state = self._transition(state, target, "+".join(names))
        return state

    def _activate_reviewed_memory(self, state, accepted_ids):
        if any(
            self.loop.memory_store.get(candidate_id).get("status") != "active"
            for candidate_id in accepted_ids
        ):
            raise ValueError("accepted candidates are not active memory")
        return self._transition(
            state,
            "memory_activated",
            "active_memory",
            active_memory_ids=accepted_ids,
        )

    def _review_state(self, state, run_status):
        if state["state"] in _TERMINAL | {"memory_activated", "verification_scheduled"}:
            return state
        candidate_ids = list(run_status.get("candidate_ids") or [])
        decisions = dict(run_status.get("review_decisions") or {})
        state = self._save(state, candidate_ids=candidate_ids, review_decisions=decisions)
        if not candidate_ids:
            if state["state"] == "candidate_created":
                return self._transition(
                    state,
                    "verified",
                    "no_candidates",
                    verification_reason="no_update_required",
                )
            return state
        if state["state"] == "candidate_created":
            state = self._transition(state, "awaiting_review", "candidate_ids")
        if set(candidate_ids) - set(decisions):
            return state
        accepted_ids = sorted(
            candidate_id
            for candidate_id in candidate_ids
            if decisions.get(candidate_id) == "accept"
        )
        if not accepted_ids:
            return self._transition(state, "rejected", "review_decisions")
        if state["state"] == "awaiting_review":
            state = self._transition(
                state,
                "accepted",
                "review_decisions",
                accepted_candidate_ids=accepted_ids,
            )
        return self._activate_reviewed_memory(state, accepted_ids)

    def _register_verification(self, state, task):
        validate_task(task)
        if task["run_id"] == state["run_id"]:
            raise ValueError("verification run_id must differ from the baseline run_id")
        if task.get("baseline_run_id") != state["run_id"]:
            raise ValueError("verification task baseline_run_id must match the auto-loop run_id")
        baseline = self.loop._load_run(state["run_id"])["task"]
        if task["workspace"] != baseline["workspace"]:
            raise ValueError("verification task must use the baseline workspace")
        if task.get("project") != baseline.get("project"):
            raise ValueError("verification task must use the baseline project")
        digest = _digest_text(_canonical(task))
        if state.get("verification_task_digest") not in (None, digest):
            raise ValueError("verification task changed after scheduling")
        return self._save(
            state,
            verification_run_id=task["run_id"],
            verification_task_digest=digest,
            verification_task=task,
        )

    @staticmethod
    def _validate_options(minimum_improvement, require_nonempty_exclusion_evidence):
        if (
            isinstance(minimum_improvement, bool)
            or not isinstance(minimum_improvement, (int, float))
            or minimum_improvement < 0
        ):
            raise ValueError("minimum_improvement must be a non-negative number")
        if not isinstance(require_nonempty_exclusion_evidence, bool):
            raise ValueError("require_nonempty_exclusion_evidence must be a boolean")

    def advance(
        self,
        task,
        *,
        verification_task=None,
        minimum_improvement=0.2,
        require_nonempty_exclusion_evidence=False,
    ):
        self._validate_options(minimum_improvement, require_nonempty_exclusion_evidence)
        state = self._load_or_create(task)
        if state["state"] in _TERMINAL:
            return self.status(state["run_id"])
        try:
            if state["state"] != "verification_scheduled":
                run_status = self.loop.execute(task)
                state = self._review_state(
                    self._reconcile_artifacts(state, run_status), run_status
                )
            if state["state"] in _TERMINAL or state["state"] == "awaiting_review":
                self._save(state, last_error=None)
                return self.status(state["run_id"])
            if state["state"] == "memory_activated":
                if verification_task is None:
                    self._save(state, last_error=None)
                    return self.status(state["run_id"])
                state = self._transition(
                    self._register_verification(state, verification_task),
                    "verification_scheduled",
                    "verification_task",
                )
            elif state["state"] == "verification_scheduled":
                if verification_task is not None:
                    state = self._register_verification(state, verification_task)
                verification_task = verification_task or state.get("verification_task")
                if verification_task is None:
                    return self.status(state["run_id"])
            else:
                self._save(state, last_error=None)
                return self.status(state["run_id"])

            self.loop.execute(verification_task)
            comparison = self.loop.compare(
                state["run_id"],
                verification_task["run_id"],
                minimum_improvement=minimum_improvement,
                require_nonempty_exclusion_evidence=require_nonempty_exclusion_evidence,
            )
            verification_status = self.loop.status(verification_task["run_id"])
            self._transition(
                state,
                "verified" if comparison["passed"] else "verification_failed",
                "comparison.json",
                verification_state=verification_status["state"],
                comparison_passed=comparison["passed"],
                comparison_path=verification_status["artifacts"].get("comparison.json"),
                last_error=None,
            )
            return self.status(state["run_id"])
        except Exception as exc:
            try:
                state = self._reconcile_artifacts(state, self.loop.status(task["run_id"]))
            except Exception:
                pass
            self._record_error(state, exc)
            raise

    def status(self, run_id):
        state = _read_json(self._path(run_id))
        output = dict(state)
        output["loop_status"] = self.loop.status(run_id)
        output["state_file"] = str(self._path(run_id))
        if state.get("verification_run_id"):
            try:
                output["verification_status"] = self.loop.status(
                    state["verification_run_id"]
                )
            except ValueError:
                output["verification_status"] = None
        return output


__all__ = ["AutoUpdateLoopCoordinator"]
