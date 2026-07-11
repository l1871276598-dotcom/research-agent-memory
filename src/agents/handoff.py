from pathlib import Path

from handoff import update_project_handoff

from .base import BaseAgent
from .orchestrator import _input, _optional_text, _task_value, _text

_WORKSPACE_CHOICES = {"personal", "work"}


class HandoffAgent(BaseAgent):
    agent_id = "handoff_agent"
    handles = ["handoff.write", "handoff.update"]

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve(strict=False)

    def run(self, task, context):
        values = _input(self, task, {"project_slug", "content", "expected_sha256", "workspace"})
        project_slug = _text(values.get("project_slug"), "input.project_slug")
        content = _text(values.get("content"), "input.content")
        expected_sha256 = _optional_text(values.get("expected_sha256"), "input.expected_sha256")
        workspace = _text(_task_value(task, values, "workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("workspace must be personal or work")
        return self.result(
            task,
            update_project_handoff(
                self.root, project_slug, content, expected_sha256, workspace=workspace
            ),
        )


__all__ = ["HandoffAgent"]
