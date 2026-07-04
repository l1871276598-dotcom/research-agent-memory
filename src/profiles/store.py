import contextvars
import json
import re
from contextlib import contextmanager
from pathlib import Path


_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_WORKSPACES = {"personal", "work"}
_CONFIDENTIALITY = {"public", "personal", "internal", "restricted"}
_CURRENT = contextvars.ContextVar("laos_profile", default=None)


def current_profile():
    value = _CURRENT.get()
    return dict(value) if isinstance(value, dict) else None


class ProfileStore:
    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "profiles.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({"profiles": {}})

    def _load(self):
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("profiles"), dict):
            raise ValueError("invalid profile store")
        return value

    def _save(self, value):
        self.path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _validate(name, workspace, project, confidentiality):
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise ValueError("invalid profile name")
        if workspace not in _WORKSPACES:
            raise ValueError("invalid profile workspace")
        if project is not None and (not isinstance(project, str) or not project.strip()):
            raise ValueError("invalid profile project")
        if confidentiality not in _CONFIDENTIALITY:
            raise ValueError("invalid profile confidentiality")

    def create(
        self,
        name,
        *,
        workspace,
        project=None,
        confidentiality="personal",
        enabled_extensions=None,
        allowed_tools=None,
    ):
        self._validate(name, workspace, project, confidentiality)
        value = self._load()
        if name in value["profiles"]:
            raise ValueError("profile already exists")
        profile = {
            "name": name,
            "workspace": workspace,
            "project": project,
            "confidentiality": confidentiality,
            "enabled_extensions": sorted(set(enabled_extensions or [])),
            "allowed_tools": sorted(set(allowed_tools or [])),
        }
        value["profiles"][name] = profile
        self._save(value)
        return dict(profile)

    def get(self, name):
        value = self._load()["profiles"].get(name)
        if not isinstance(value, dict):
            raise ValueError("profile not found")
        return dict(value)

    def list(self):
        return [dict(value) for _, value in sorted(self._load()["profiles"].items())]

    @contextmanager
    def activate(self, name):
        profile = self.get(name)
        token = _CURRENT.set(profile)
        try:
            yield dict(profile)
        finally:
            _CURRENT.reset(token)

    def task_scope(self, name, task):
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        profile = self.get(name)
        scoped = dict(task)
        scoped["workspace"] = profile["workspace"]
        if profile["project"] is None:
            scoped.pop("project", None)
        else:
            scoped["project"] = profile["project"]
        return scoped
