import json
from pathlib import Path

from .base import BaseAgent, _task_type


ENTRY_KEYS = {"id", "class", "handles"}


class AgentRegistry:
    def __init__(self, agents):
        try:
            agent_list = list(agents)
        except TypeError as exc:
            raise ValueError("agents must be iterable") from exc

        self._agents_by_id = {}
        self._agents_by_handle = {}
        for agent in agent_list:
            if not isinstance(agent, BaseAgent):
                raise ValueError("registry entries must be BaseAgent instances")
            agent_id = agent.agent_id
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("agent_id must be a non-empty string")
            if agent_id in self._agents_by_id:
                raise ValueError("duplicate agent_id")
            if not isinstance(agent.handles, list):
                raise ValueError("agent handles must be a list")
            seen_handles = set()
            for handle in agent.handles:
                if not isinstance(handle, str) or not handle.strip():
                    raise ValueError("agent handles must be non-empty strings")
                if handle in seen_handles or handle in self._agents_by_handle:
                    raise ValueError("duplicate agent handle")
                seen_handles.add(handle)
                self._agents_by_handle[handle] = agent
            self._agents_by_id[agent_id] = agent

    def select(self, task):
        task_type = _task_type(task)
        try:
            return self._agents_by_handle[task_type]
        except KeyError as exc:
            raise ValueError("task type is not registered") from exc

    @classmethod
    def from_config(cls, path, agents):
        registry = cls(agents)
        try:
            config = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid registry config") from exc
        if not isinstance(config, dict) or set(config) != {"agents"}:
            raise ValueError("registry config must contain only agents")
        entries = config["agents"]
        if not isinstance(entries, list):
            raise ValueError("registry config agents must be a list")

        configured_ids = set()
        configured_handles = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
                raise ValueError("invalid registry agent entry")
            agent_id = entry["id"]
            class_name = entry["class"]
            handles = entry["handles"]
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("configured agent id must be a non-empty string")
            if not isinstance(class_name, str) or not class_name.strip():
                raise ValueError("configured class must be a non-empty string")
            if not isinstance(handles, list):
                raise ValueError("configured handles must be a list")
            if agent_id in configured_ids:
                raise ValueError("duplicate configured agent id")
            configured_ids.add(agent_id)
            for handle in handles:
                if not isinstance(handle, str) or not handle.strip():
                    raise ValueError("configured handles must be non-empty strings")
                if handle in configured_handles:
                    raise ValueError("duplicate configured agent handle")
                configured_handles.add(handle)

            agent = registry._agents_by_id.get(agent_id)
            if agent is None:
                raise ValueError("configured agent is not supplied")
            if type(agent).__name__ != class_name:
                raise ValueError("configured class does not match supplied agent")
            if agent.handles != handles:
                raise ValueError("configured handles do not match supplied agent")

        if configured_ids != set(registry._agents_by_id):
            raise ValueError("supplied agents do not match registry config")
        return registry
