from agents.base import validate_result
from agents.registry import AgentRegistry


def _validate_task(task):
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    task_type = task.get("type")
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task.type must be a non-empty string")
    if "input" in task and not isinstance(task["input"], dict):
        raise ValueError("task.input must be an object")
    if "context_limit" in task:
        context_limit = task["context_limit"]
        if isinstance(context_limit, bool) or not isinstance(context_limit, int) or context_limit <= 0:
            raise ValueError("task.context_limit must be a positive integer")
    return task_type


class Orchestrator:
    def __init__(self, registry):
        if not isinstance(registry, AgentRegistry):
            raise ValueError("registry must be an AgentRegistry")
        self.registry = registry

    def run(self, task):
        task_type = _validate_task(task)
        if task_type == "context.build":
            target = self.registry.select(task)
            result = target.run(task, {})
            validate_result(result, target, task)
            return result

        context_task = {
            "type": "context.build",
            "input": {"task": task},
            "context_limit": task.get("context_limit", 8000),
        }
        context_agent = self.registry.select(context_task)
        context_result = context_agent.run(context_task, {})
        validate_result(context_result, context_agent, context_task)

        target = self.registry.select(task)
        result = target.run(task, context_result["output"])
        validate_result(result, target, task)
        return result
