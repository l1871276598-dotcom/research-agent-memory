from __future__ import annotations

from typing import Any


class ConversationReviewCoordinator:
    """Trigger review after durable per-session thresholds are reached."""

    def __init__(
        self,
        service: Any,
        state: Any,
        *,
        procedure_proposals: Any = None,
        memory_interval: int = 10,
        skill_interval: int = 10,
    ) -> None:
        if not callable(getattr(service, "review", None)):
            raise ValueError("conversation review service is invalid")
        for name in ("advance", "complete", "fail"):
            if not callable(getattr(state, name, None)):
                raise ValueError("review state store is invalid")
        if procedure_proposals is not None and not callable(
            getattr(procedure_proposals, "create", None)
        ):
            raise ValueError("procedure proposal store is invalid")
        for value in (memory_interval, skill_interval):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("review intervals must be positive integers")
        self.service = service
        self.state = state
        self.procedure_proposals = procedure_proposals
        self.memory_interval = memory_interval
        self.skill_interval = skill_interval

    def _save_procedures(self, candidates, session_id):
        if not candidates:
            return []
        if self.procedure_proposals is None:
            return []
        saved = []
        for item in candidates:
            value = dict(item)
            content = value.get("content") or value.pop("change", None)
            value["content"] = content
            value.setdefault("action", "update")
            value.setdefault("summary", str(content or "")[:160])
            refs = list(value.get("source_refs") or [])
            reference = f"session:{session_id}"
            if reference not in refs:
                refs.append(reference)
            value["source_refs"] = refs
            saved.append(self.procedure_proposals.create(value))
        return saved

    def record_turn(
        self,
        *,
        session_id: str,
        messages: list[dict],
        workspace: str,
        confidentiality: str = "personal",
        project: str | None = None,
        tool_iterations: int = 0,
        force: bool = False,
        turn_increment: int = 1,
    ) -> dict:
        if confidentiality == "restricted":
            return {
                "status": "skipped",
                "reason": "restricted_conversation",
                "session_id": session_id,
            }

        counters = self.state.advance(
            session_id,
            tool_iterations,
            turn_increment=turn_increment,
        )
        review_memory = force or counters["turns"] >= self.memory_interval
        review_skills = force or counters["tool_iterations"] >= self.skill_interval
        if not review_memory and not review_skills:
            return {
                "status": "not_due",
                "session_id": session_id,
                "counters": counters,
            }

        try:
            result = self.service.review(
                messages,
                workspace=workspace,
                confidentiality=confidentiality,
                project=project,
                session_id=session_id,
                review_memory=review_memory,
                review_skills=review_skills,
                routed=True,
            )
            result["procedure_proposals"] = self._save_procedures(
                result.get("skill_candidates", []),
                session_id,
            )
        except Exception as exc:
            failed = self.state.fail(session_id, str(exc))
            return {
                "status": "failed",
                "session_id": session_id,
                "error": str(exc),
                "counters": failed,
            }

        completed = self.state.complete(
            session_id,
            memory_reviewed=review_memory,
            skills_reviewed=review_skills,
        )
        return {
            "status": "reviewed",
            "session_id": session_id,
            "review_memory": review_memory,
            "review_skills": review_skills,
            "result": result,
            "counters": completed,
        }
