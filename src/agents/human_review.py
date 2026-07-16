from human_review.review_store import ReviewError

from .base import BaseAgent


class HumanReviewAgent(BaseAgent):
    agent_id = "human_review_agent"
    handles = ["review.list", "review.show", "review.decide"]

    def __init__(self, store):
        self.store = store

    def run(self, task, context):
        if not isinstance(task, dict) or set(task) != {"type", "input"}:
            raise ReviewError("invalid_request")
        values = task["input"]
        if not isinstance(values, dict):
            raise ReviewError("invalid_request")
        task_type = task["type"]
        if task_type == "review.list":
            if values:
                raise ReviewError("invalid_request")
            return self.store.list()
        if task_type == "review.show":
            if not set(values) <= {"rq_id"}:
                raise ReviewError("invalid_request")
            return self.store.show(values.get("rq_id"))
        if task_type == "review.decide":
            keys = {
                "rq_id", "decision_seq", "action", "operator_claim",
                "reason",
            }
            if not set(values) <= keys:
                raise ReviewError("invalid_request")
            return self.store.decide(
                values.get("rq_id"),
                values.get("decision_seq"),
                values.get("action"),
                values.get("operator_claim"),
                values.get("reason"),
            )
        raise ReviewError("invalid_request")


__all__ = ["HumanReviewAgent"]
