import argparse
import contextlib
import io
from pathlib import Path

from memory import WORKSPACE_CHOICES
from memory.candidate import validate_candidate_values
from review.authority import ReviewerProfile

from .base import BaseAgent


_WORKSPACE_CHOICES = frozenset(WORKSPACE_CHOICES)
_LEGACY_CONTEXT_FREE_TASKS = frozenset(
    {
        "import.file",
        "import.chatgpt",
        "memory.review",
        "memory.activate",
        "loop.reflect",
        "loop.suggest-policies",
        "loop.generate-candidate",
        "loop.coordinate",
        "review.list",
        "review.show",
        "review.decide",
    }
)
_CANDIDATE_STATUSES = frozenset({"candidate", "conflicted"})


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


def _limit(values):
    limit = values.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50
    ):
        raise ValueError("input.limit must be between 1 and 50")
    return limit


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
        values = _input(self, task, {"query", "workspace", "project", "limit"})
        query = _text(values.get("query"), "input.query")
        workspace = _text(_task_value(task, values, "workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("input.workspace must be personal or work")
        project = _optional_text(_task_value(task, values, "project"), "project")
        limit = _limit(values)
        results = self.core.search(query, workspace, project)
        if not isinstance(results, list):
            raise ValueError("memory search result must be a list")
        if limit is not None:
            results = results[:limit]
        return self.result(task, {"results": results})


class CandidateListAgent(BaseAgent):
    agent_id = "candidate_list_agent"
    handles = ["memory.candidates"]

    def __init__(self, core):
        if not callable(getattr(core, "reviewable", None)):
            raise ValueError("memory core is invalid")
        self.core = core

    @staticmethod
    def _statuses(values):
        raw = values.get("statuses", values.get("status"))
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ValueError("input.status must be candidate or conflicted")
        statuses = []
        for item in raw:
            if item == "conflict":
                item = "conflicted"
            if item not in _CANDIDATE_STATUSES:
                raise ValueError("input.status must be candidate or conflicted")
            statuses.append(item)
        return statuses

    @staticmethod
    def _summary(record):
        content = " ".join(str(record.get("content", "")).split())
        preview = content[:240]
        if len(content) > len(preview):
            preview += "…"
        fields = (
            "id",
            "type",
            "title",
            "status",
            "audit_status",
            "candidate_action",
            "requested_action",
            "scope",
            "workspace",
            "project",
            "confidentiality",
            "confidence",
            "source",
            "created",
            "updated",
            "reviewed_at",
            "target_id",
            "source_id",
            "relative_path",
            "tags",
            "source_refs",
        )
        item = {name: record[name] for name in fields if name in record}
        item["content_preview"] = preview
        return item

    def run(self, task, context):
        values = _input(
            self,
            task,
            {"workspace", "project", "status", "statuses", "limit"},
        )
        workspace = _text(_task_value(task, values, "workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("input.workspace must be personal or work")
        project = _optional_text(_task_value(task, values, "project"), "project")
        limit = _limit(values)
        results = self.core.reviewable(
            workspace,
            project,
            self._statuses(values),
        )
        if not isinstance(results, list):
            raise ValueError("memory candidates result must be a list")
        if limit is not None:
            results = results[:limit]
        return self.result(
            task,
            {"results": [self._summary(record) for record in results]},
        )


class ReviewAgent(BaseAgent):
    agent_id = "review_agent"
    handles = ["memory.review"]

    def __init__(self, gate, default_workspace=None):
        if not callable(getattr(gate, "review", None)):
            raise ValueError("review gate is invalid")
        if default_workspace is not None and default_workspace not in _WORKSPACE_CHOICES:
            raise ValueError("default workspace must be personal or work")
        reviewer_profile = getattr(gate, "reviewer_profile", None)
        if reviewer_profile is None:
            workspace = default_workspace or "personal"
            reviewer_profile = ReviewerProfile(
                workspace,
                "personal" if workspace == "personal" else "internal",
            )
        if not isinstance(reviewer_profile, ReviewerProfile):
            raise ValueError("review gate has an invalid reviewer profile")
        if (
            default_workspace is not None
            and default_workspace != reviewer_profile.workspace
        ):
            raise ValueError("default workspace conflicts with reviewer profile")
        self.gate = gate
        self.reviewer_profile = reviewer_profile

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
        if workspace is not None:
            workspace = _text(workspace, "workspace")
            if workspace not in _WORKSPACE_CHOICES:
                raise ValueError("workspace must be personal or work")
        else:
            workspace = self.reviewer_profile.workspace
        output = self.gate.review(
            action,
            candidate_id,
            reason=reason,
            workspace=workspace,
        )

        return self.result(task, output)


class ActivationAgent(BaseAgent):
    agent_id = "activation_agent"
    handles = ["memory.activate"]

    def __init__(self, gate):
        if not callable(getattr(gate, "activate", None)):
            raise ValueError("activation gate is invalid")
        self.gate = gate

    def run(self, task, context):
        values = _input(
            self,
            task,
            {"decision_id", "expected_active_generation"},
        )
        decision_id = _text(values.get("decision_id"), "input.decision_id")
        generation = values.get("expected_active_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(
                "input.expected_active_generation must be a non-negative integer"
            )
        output = self.gate.activate(decision_id, generation)
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


__all__ = [
    "ActivationAgent",
    "CandidateListAgent",
    "ContextAgent",
    "ImportAgent",
    "MemoryAgent",
    "ReviewAgent",
    "SearchAgent",
]
