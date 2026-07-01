from __future__ import annotations

from typing import Any


class ConversationReviewCoordinator:
    """Trigger review after durable per-session thresholds are reached."""

    def __init__(
        self,
        service: Any,
        state: Any,
        *,
        memory_interval: int = 10,
        skill_interval: int = 10,
    ) -> None:
        if not callable(getattr(service, "review", None)):
            raise ValueError("conversation review service is invalid")
        for name in ("advance", "complete", "fail"):
            if not callable(getattr(state, name, None)):
                raise ValueError("review state store is invalid")
        for value in (memory_interval, skill_interval):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("review intervals must be positive integers")
        self.service = service
        self.state = state
        self.memory_interval = memory_interval
        self.skill_interval = skill_interval

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
    ) -> dict:
        if confidentiality == "restricted":
            return {
                "status": "skipped",
                "reason": "restricted_conversation",
                "session_id": session_id,
            }

        counters = self.state.advance(session_id, tool_iterations)
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
