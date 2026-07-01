import json
from pathlib import Path

from .base import BaseAgent


class HermesRuntimeAgent(BaseAgent):
    agent_id = "hermes_runtime_agent"
    handles = ["runtime.hermes.status"]

    def __init__(self, repo_root=None):
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2])

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")
        values = task.get("input", {})
        if not isinstance(values, dict) or values:
            raise ValueError("runtime.hermes.status does not accept input keys")
        manifest = json.loads(
            (self.repo_root / "config" / "hermes_vendor.json").read_text(encoding="utf-8")
        )
        destination = self.repo_root / manifest["destination"]
        metadata_path = destination / "_UPSTREAM.json"
        metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.is_file()
            else {}
        )
        output = {
            "available": (destination / "run_agent.py").is_file(),
            "destination": manifest["destination"],
            "pinned_ref": manifest.get("pinned_ref"),
            "installed_commit": metadata.get("commit"),
            "disabled_surfaces": manifest.get("disabled_surfaces", []),
            "upstream": manifest["upstream"],
        }
        return self.result(task, output)


__all__ = ["HermesRuntimeAgent"]
