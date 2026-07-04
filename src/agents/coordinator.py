import json
from pathlib import Path

import memory_agent

from .base import BaseAgent
from .policy import POLICY_CONTRACT_ID


_ALLOWED_INPUTS = {
    "task",
    "result",
    "outcome",
    "error",
    "reflection",
    "root_cause",
    "next_change",
    "workspace",
    "project",
}
_REQUIRED_INPUTS = {"task", "result", "outcome", "workspace"}


def _values(task):
    values = task.get("input")
    if not isinstance(values, dict) or not set(values) <= _ALLOWED_INPUTS:
        raise ValueError("task.input has unsupported keys")
    if not _REQUIRED_INPUTS <= set(values):
        raise ValueError("task.input is missing required fields")
    if not isinstance(values["task"], str) or not values["task"].strip():
        raise ValueError("task.input.task must be a non-empty string")
    if not isinstance(values["result"], str):
        raise ValueError("task.input.result must be a string")
    if values["outcome"] not in {"pass", "fail"}:
        raise ValueError("task.input.outcome must be pass or fail")
    if values["workspace"] not in {"personal", "work"}:
        raise ValueError("task.input.workspace must be personal or work")
    for name in ("error", "reflection", "root_cause", "next_change"):
        if name in values and not isinstance(values[name], str):
            raise ValueError(f"task.input.{name} must be a string")
    project = values.get("project")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise ValueError("task.input.project must be a non-empty string")
    return values


def _policy_candidates(state_dir, relative_path, run_id):
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("policy artifact path is invalid")
    state = Path(state_dir).expanduser()
    path = state / relative_path
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("policy artifact path is unsafe")
        if not path.resolve(strict=True).is_relative_to(state.resolve(strict=True)):
            raise ValueError("policy artifact escaped the state directory")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("policy artifact is invalid") from exc
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("contract_id") != POLICY_CONTRACT_ID
        or payload.get("run_id") != run_id
        or not isinstance(candidates, list)
    ):
        raise ValueError("policy artifact is invalid")
    policy_ids = []
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("policy_id"), str):
            raise ValueError("policy artifact is invalid")
        policy_ids.append(item["policy_id"])
    if len(policy_ids) != len(set(policy_ids)):
        raise ValueError("policy artifact contains duplicate policy IDs")
    return policy_ids


class LoopCoordinatorAgent(BaseAgent):
    agent_id = "loop_coordinator_agent"
    handles = ["loop.coordinate"]

    def __init__(
        self,
        root,
        state_dir,
        reflection_agent,
        policy_agent,
        candidate_agent,
    ):
        if isinstance(root, bool) or isinstance(state_dir, bool):
            raise ValueError("coordinator paths must be filesystem paths")
        self.root = Path(root).expanduser()
        self.state_dir = Path(state_dir).expanduser()
        for agent, handle in (
            (reflection_agent, "loop.reflect"),
            (policy_agent, "loop.suggest-policies"),
            (candidate_agent, "loop.generate-candidate"),
        ):
            if not callable(getattr(agent, "run", None)) or handle not in getattr(
                agent, "handles", []
            ):
                raise ValueError("coordinator agent dependency is invalid")
        self.reflection_agent = reflection_agent
        self.policy_agent = policy_agent
        self.candidate_agent = candidate_agent

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")
        values = _values(task)
        finalized = memory_agent.finalize_memory(
            self.root,
            values["task"],
            values["result"],
            state_dir=self.state_dir,
            outcome=values["outcome"],
            error=values.get("error"),
            reflection=values.get("reflection"),
            root_cause=values.get("root_cause"),
            next_change=values.get("next_change"),
            workspace=values["workspace"],
            project=values.get("project"),
        )
        run_id = finalized["loop_run"]["run_id"]
        reflection_result = self.reflection_agent.run(
            {"type": "loop.reflect", "input": {"run_id": run_id}}, {}
        )
        policy_result = self.policy_agent.run(
            {"type": "loop.suggest-policies", "input": {"run_id": run_id}}, {}
        )
        policy_ids = _policy_candidates(
            self.state_dir,
            policy_result["output"]["artifact"],
            run_id,
        )
        evaluations = []
        generated = []
        for policy_id in policy_ids:
            candidate_input = {
                "policy_id": policy_id,
                "workspace": values["workspace"],
            }
            if values.get("project") is not None:
                candidate_input["project"] = values["project"].strip()
            candidate_result = self.candidate_agent.run(
                {"type": "loop.generate-candidate", "input": candidate_input}, {}
            )
            evaluations.append(candidate_result["output"])
            generated.extend(candidate_result["candidates"])

        _, run_data, _ = memory_agent._load_loop_run(self.state_dir, run_id)
        reflection = reflection_result["output"]
        policy = policy_result["output"]
        output = {
            "run_id": run_id,
            "finalize_reused": finalized["loop_run"]["reused"],
            "session_candidate_ids": list(run_data["candidate_ids"]),
            "reflection": {
                "artifact": reflection["artifact"],
                "reused": reflection["reused"],
                "likely_cause": reflection["reflection"]["likely_cause"],
                "reusable_lesson": reflection["reflection"]["reusable_lesson"],
                "unknown_fields": list(
                    reflection["reflection"]["unknown_fields"]
                ),
            },
            "policy": {
                "artifact": policy["artifact"],
                "review_artifact": policy["review_artifact"],
                "reused": policy["reused"],
                "candidate_count": policy["candidate_count"],
                "reviewable_count": policy["reviewable_count"],
                "duplicate_count": policy["duplicate_count"],
                "conflicted_count": policy["conflicted_count"],
            },
            "candidate_generation": evaluations,
            "generated_candidate_ids": list(dict.fromkeys(generated)),
            "applied": False,
        }
        return self.result(
            task,
            output,
            candidates=output["generated_candidate_ids"],
            confidence=1.0,
        )


__all__ = ["LoopCoordinatorAgent"]
