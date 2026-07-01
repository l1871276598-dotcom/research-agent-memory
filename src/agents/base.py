import json
import uuid
from numbers import Real


RESULT_KEYS = {
    "agent_id",
    "task_type",
    "output",
    "candidates",
    "requires_review",
    "metadata",
}
METADATA_KEYS = {"trace_id", "confidence"}


def _agent_id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("agent_id must be a non-empty string")
    return value


def _task_type(task):
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    task_type = task.get("type")
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task.type must be a non-empty string")
    return task_type


def _confidence(value):
    if isinstance(value, bool) or not isinstance(value, Real) or not 0 <= value <= 1:
        raise ValueError("confidence must be a number between 0 and 1")
    return float(value)


def _trace_id(task):
    if "trace_id" in task:
        value = task["trace_id"]
        if not isinstance(value, str):
            raise ValueError("task.trace_id must be a UUID string")
        try:
            return str(uuid.UUID(value))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("task.trace_id must be a UUID string") from exc
    try:
        canonical = json.dumps(
            task,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("task must be JSON serializable") from exc
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical))


class BaseAgent:
    agent_id: str = ""
    handles: list[str] = []

    def can_handle(self, task) -> bool:
        return isinstance(task, dict) and task.get("type") in self.handles

    def run(self, task, context) -> dict:
        raise NotImplementedError

    def result(self, task, output, candidates=None, confidence=1.0) -> dict:
        agent_id = _agent_id(self.agent_id)
        task_type = _task_type(task)
        if not isinstance(output, dict):
            raise ValueError("output must be an object")
        if candidates is None:
            candidates = []
        if not isinstance(candidates, list) or not all(isinstance(item, str) for item in candidates):
            raise ValueError("candidates must be a list of strings")
        return {
            "agent_id": agent_id,
            "task_type": task_type,
            "output": output,
            "candidates": list(candidates),
            "requires_review": True,
            "metadata": {
                "trace_id": _trace_id(task),
                "confidence": _confidence(confidence),
            },
        }


def validate_result(result, agent, task):
    task_type = _task_type(task)
    if not isinstance(agent, BaseAgent):
        raise ValueError("agent must be a BaseAgent")
    agent_id = _agent_id(agent.agent_id)
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        raise ValueError("result has an invalid schema")
    if result["agent_id"] != agent_id:
        raise ValueError("result.agent_id does not match agent")
    if result["task_type"] != task_type:
        raise ValueError("result.task_type does not match task")
    if not isinstance(result["output"], dict):
        raise ValueError("result.output must be an object")
    candidates = result["candidates"]
    if not isinstance(candidates, list) or not all(isinstance(item, str) for item in candidates):
        raise ValueError("result.candidates must be a list of strings")
    if result["requires_review"] is not True:
        raise ValueError("result.requires_review must be true")
    metadata = result["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != METADATA_KEYS:
        raise ValueError("result.metadata has an invalid schema")
    trace_id = metadata["trace_id"]
    if not isinstance(trace_id, str):
        raise ValueError("result.metadata.trace_id must be a UUID string")
    try:
        uuid.UUID(trace_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("result.metadata.trace_id must be a UUID string") from exc
    _confidence(metadata["confidence"])
    return result
