import argparse
import contextlib
import io
from pathlib import Path

from memory import WORKSPACE_CHOICES
from memory.candidate import validate_candidate_values

from .base import BaseAgent


_WORKSPACE_CHOICES = frozenset(WORKSPACE_CHOICES)
_LEGACY_CONTEXT_FREE_TASKS = frozenset(
    {
        "import.file",
        "import.chatgpt",
        "memory.review",
        "loop.reflect",
        "loop.suggest-policies",
        "loop.generate-candidate",
        "loop.coordinate",
    }
)


def _input(agent, task, allowed=None):
    if not agent.can_handle(task):
        raise ValueError("task type is not handled by this agent")
    values = task.get("input")
    if not isinstance(values, dict):
        raise ValueError("task.input must be an object")
    if allowed is not None and not set(values) <= allowed:
        raise ValueError("task.input has unsupported keys")
    return values


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_text(value, name):
    if value is not None:
        return _text(value, name)
    return None


def _task_value(task, values, name):
    if name in task:
        return task[name]
    return values.get(name)


class ImportAgent(BaseAgent):
    agent_id = "import_agent"
    handles = ["import.file", "import.chatgpt"]

    def __init__(self, root, backend):
        if isinstance(root, bool):
            raise ValueError("root must be a filesystem path")
        try:
            self.root = Path(root).expanduser()
        except TypeError as exc:
            raise ValueError("root must be a filesystem path") from exc
        for name in ("import_manual", "import_chatgpt"):
            if not callable(getattr(backend, name, None)):
                raise ValueError("import backend is invalid")
        self.backend = backend

    def run(self, task, context):
        values = _input(self, task, {"path"})
        path = _text(values.get("path"), "input.path")
        common = {"root": str(self.root), "dry_run": False}
        if task["type"] == "import.file":
            function = self.backend.import_manual
            args = argparse.Namespace(path=path, **common)
        else:
            function = self.backend.import_chatgpt
            args = argparse.Namespace(zip=path, **common)
        with contextlib.redirect_stdout(io.StringIO()):
            output = function(args)
        return self.result(task, output)


class MemoryAgent(BaseAgent):
    agent_id = "memory_agent"
    handles = ["memory.create"]

    def __init__(self, core):
        if not callable(getattr(core, "create_candidate", None)):
            raise ValueError("memory core is invalid")
        self.core = core

    def run(self, task, context):
        values = _input(self, task)
        candidate = dict(values)
        for name in ("workspace", "project"):
            if name in task:
                candidate[name] = task[name]
        validate_candidate_values(candidate)
        output = self.core.create_candidate(candidate)
        if (
            not isinstance(output, dict)
            or output.get("status") != "candidate"
            or not isinstance(output.get("candidate_id"), str)
            or not output["candidate_id"].strip()
        ):
            raise ValueError("memory core did not create a candidate")
        return self.result(task, output, [output["candidate_id"]])


class SearchAgent(BaseAgent):
    agent_id = "search_agent"
    handles = ["memory.search"]

    def __init__(self, core):
        if not callable(getattr(core, "search", None)):
            raise ValueError("memory core is invalid")
        self.core = core

    def run(self, task, context):
        values = _input(self, task, {"query", "workspace", "project"})
        query = _text(values.get("query"), "input.query")
        workspace = _text(_task_value(task, values, "workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("input.workspace must be personal or work")
        project = _optional_text(_task_value(task, values, "project"), "project")
        results = self.core.search(query, workspace, project)
        if not isinstance(results, list):
            raise ValueError("memory search result must be a list")
        return self.result(task, {"results": results})


class ReviewAgent(BaseAgent):
    agent_id = "review_agent"
    handles = ["memory.review"]

    def __init__(self, gate, default_workspace=None):
        if not callable(getattr(gate, "review", None)):
            raise ValueError("review gate is invalid")
        if default_workspace is not None and default_workspace not in _WORKSPACE_CHOICES:
            raise ValueError("default workspace must be personal or work")
        self.gate = gate
        self.default_workspace = default_workspace

    def run(self, task, context):
        values = _input(
            self,
            task,
            {"action", "candidate_id", "reason", "workspace"},
        )
        action = _text(values.get("action"), "input.action")
        candidate_id = _text(values.get("candidate_id"), "input.candidate_id")
        reason = _optional_text(values.get("reason"), "input.reason")

        workspace = _task_value(task, values, "workspace")
        if workspace is None:
            workspace = self.default_workspace

        if workspace is None:
            output = self.gate.review(action, candidate_id, reason=reason)
        else:
            workspace = _text(workspace, "workspace")
            if workspace not in _WORKSPACE_CHOICES:
                raise ValueError("workspace must be personal or work")
            output = self.gate.review(
                action,
                candidate_id,
                reason=reason,
                workspace=workspace,
            )

        return self.result(task, output)


class ContextAgent(BaseAgent):
    agent_id = "context_agent"
    handles = ["context.build"]

    def __init__(self, builder):
        if not callable(getattr(builder, "build", None)):
            raise ValueError("context builder is invalid")
        self.builder = builder

    def run(self, task, context):
        values = _input(self, task, {"query", "task", "limit", "workspace", "project"})
        embedded = values.get("task")
        if isinstance(embedded, dict):
            original_type = _text(embedded.get("type"), "input.task.type")
            original_input = embedded.get("input", {})
            if not isinstance(original_input, dict):
                raise ValueError("input.task.input must be an object")
            query = original_input.get("query") or original_input.get("task") or original_type
            workspace = _task_value(task, values, "workspace")
            if "workspace" not in task:
                workspace = _task_value(embedded, original_input, "workspace")
            project = _task_value(task, values, "project")
            if "project" not in task:
                project = _task_value(embedded, original_input, "project")
        else:
            query = values.get("query") or embedded
            workspace = _task_value(task, values, "workspace")
            project = _task_value(task, values, "project")
        query = _text(query, "context query")
        limit = task.get("context_limit", values.get("limit", 8000))
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("context limit must be a positive integer")
        project = _optional_text(project, "project")
        if workspace is None:
            if not (isinstance(embedded, dict) and original_type in _LEGACY_CONTEXT_FREE_TASKS):
                raise ValueError("workspace must be a non-empty string")
            empty = getattr(self.builder, "empty", None)
            if not callable(empty):
                raise ValueError("context builder cannot build empty context")
            output = empty(limit)
        else:
            workspace = _text(workspace, "workspace")
            if workspace not in _WORKSPACE_CHOICES:
                raise ValueError("input.workspace must be personal or work")
            output = self.builder.build(query, limit, workspace, project)
        return self.result(task, output)


__all__ = ["ContextAgent", "ImportAgent", "MemoryAgent", "ReviewAgent", "SearchAgent"]
