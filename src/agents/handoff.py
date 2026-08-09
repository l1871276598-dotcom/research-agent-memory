from pathlib import Path

from handoff import update_project_handoff

from .base import BaseAgent
from .orchestrator import _input, _optional_text, _task_value, _text

_WORKSPACE_CHOICES = {"personal", "work"}


class HandoffAgent(BaseAgent):
    agent_id = "handoff_agent"
    handles = ["handoff.write", "handoff.update"]

    def __init__(self, root, state_dir):
        self.root = Path(root).expanduser().resolve(strict=False)
        self.state_dir = Path(state_dir).expanduser().resolve(strict=False)

    def run(self, task, context):
        values = _input(self, task, {"project_slug", "content", "expected_sha256", "workspace", "project"})
        project_slug = _text(values.get("project_slug"), "input.project_slug")
        content = _text(values.get("content"), "input.content")
        expected_sha256 = _optional_text(values.get("expected_sha256"), "input.expected_sha256")
        workspace = _text(_task_value(task, values, "workspace"), "workspace")
        if workspace not in _WORKSPACE_CHOICES:
            raise ValueError("workspace must be personal or work")
        # GP9-02: the Bridge injects the trusted profile project. The caller's
        # project_slug must equal it exactly — a caller cannot target another
        # project's handoff file under the same workspace.
        project = _optional_text(_task_value(task, values, "project"), "project")
        if project is not None and project_slug != project:
            raise ValueError("project_slug must match the trusted Bridge profile project")
        return self.result(
            task,
            update_project_handoff(
                self.root,
                project_slug,
                content,
                expected_sha256,
                workspace=workspace,
                state_dir=self.state_dir,
            ),
        )


__all__ = ["HandoffAgent"]
