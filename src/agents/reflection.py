from __future__ import annotations

from .base import BaseAgent


class ConversationReviewAgent(BaseAgent):
    agent_id = "conversation_review_agent"
    handles = ["reflection.prepare", "reflection.apply"]

    def __init__(self, service):
        for name in ("prepare", "apply"):
            if not callable(getattr(service, name, None)):
                raise ValueError("conversation review service is invalid")
        self.service = service

    @staticmethod
    def _input(task, allowed):
        values = task.get("input")
        if not isinstance(values, dict) or not set(values) <= allowed:
            raise ValueError("task.input is invalid")
        return values

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")

        if task["type"] == "reflection.prepare":
            values = self._input(
                task,
                {"messages", "review_memory", "review_skills", "routed", "tail"},
            )
            messages = self.service.prepare(
                values.get("messages"),
                review_memory=values.get("review_memory", True),
                review_skills=values.get("review_skills", False),
                routed=values.get("routed", False),
                tail=values.get("tail", 24),
            )
            return self.result(task, {"messages": messages}, confidence=1.0)

        values = self._input(
            task,
            {"response", "workspace", "confidentiality", "project", "session_id"},
        )
        workspace = task.get("workspace", values.get("workspace"))
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        output = self.service.apply(
            values.get("response"),
            workspace=workspace,
            confidentiality=values.get("confidentiality", "personal"),
            project=task.get("project", values.get("project")),
            session_id=values.get("session_id"),
        )
        return self.result(task, output, output["candidate_ids"], confidence=1.0)


__all__ = ["ConversationReviewAgent"]
