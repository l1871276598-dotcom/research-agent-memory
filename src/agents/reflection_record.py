from .base import BaseAgent


class ReflectionRecordAgent(BaseAgent):
    agent_id = "reflection_record_agent"
    handles = ["reflection.record"]

    def __init__(self, coordinator):
        if not callable(getattr(coordinator, "record_turn", None)):
            raise ValueError("invalid coordinator")
        self.coordinator = coordinator

    def run(self, task, context):
        values = task.get("input")
        if not isinstance(values, dict):
            raise ValueError("task.input must be an object")
        output = self.coordinator.record_turn(
            session_id=values.get("session_id"),
            messages=values.get("messages"),
            workspace=task.get("workspace", values.get("workspace")),
            confidentiality=values.get("confidentiality", "personal"),
            project=task.get("project", values.get("project")),
            tool_iterations=values.get("tool_iterations", 0),
        )
        ids = output.get("result", {}).get("candidate_ids", [])
        return self.result(task, output, ids)
