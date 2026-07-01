"""LAOS extension loader.

Discovery, explicit opt-in, register(context), hook registration, and tool
collision rules are selectively adapted from Hermes Agent plugins.py (MIT).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


VALID_HOOKS = {
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
    "pre_verify",
    "on_session_start",
    "on_session_end",
    "on_session_reset",
    "subagent_start",
    "subagent_stop",
    "pre_gateway_dispatch",
    "pre_approval_request",
    "post_approval_response",
}


@dataclass
class PluginManifest:
    name: str
    version: str = ""
    description: str = ""
    provides_tools: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    path: str = ""


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: ModuleType
    tools: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)


class PluginContext:
    def __init__(self, plugin_name, tools, hooks, allow_override=False):
        self.plugin_name = plugin_name
        self.tools = tools
        self.hooks = hooks
        self.allow_override = allow_override
        self.registered_tools = []
        self.registered_hooks = []

    def register_tool(self, name, handler):
        if not isinstance(name, str) or not name.strip() or not callable(handler):
            raise ValueError("invalid plugin tool")
        if name in self.tools and not self.allow_override:
            raise PermissionError(f"plugin tool override denied: {name}")
        self.tools[name] = handler
        self.registered_tools.append(name)

    def register_hook(self, name, callback):
        if name not in VALID_HOOKS or not callable(callback):
            raise ValueError("invalid plugin hook")
        self.hooks.setdefault(name, []).append(callback)
        self.registered_hooks.append(name)


class PluginLoader:
    def __init__(self, roots, *, enabled=None, allow_overrides=None, tools=None):
        self.roots = [Path(root).expanduser() for root in roots]
        self.enabled = set(enabled or [])
        self.allow_overrides = set(allow_overrides or [])
        self.tools = dict(tools or {})
        self.hooks = {}
        self.loaded = {}
        self.errors = {}

    @staticmethod
    def _manifest(directory):
        path = directory / "plugin.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError("plugin.json is required")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("plugin manifest must be an object")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("plugin name is required")
        hooks = value.get("provides_hooks", [])
        tools = value.get("provides_tools", [])
        if not isinstance(hooks, list) or not set(hooks) <= VALID_HOOKS:
            raise ValueError("plugin manifest has invalid hooks")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise ValueError("plugin manifest has invalid tools")
        return PluginManifest(
            name=name,
            version=str(value.get("version", "")),
            description=str(value.get("description", "")),
            provides_tools=tools,
            provides_hooks=hooks,
            path=str(directory),
        )

    @staticmethod
    def _module(directory, name):
        path = directory / "__init__.py"
        if not path.is_file() or path.is_symlink():
            raise ValueError("plugin __init__.py is required")
        spec = importlib.util.spec_from_file_location(f"laos_extension_{name}", path)
        if spec is None or spec.loader is None:
            raise ImportError("plugin module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def discover(self):
        found = {}
        for root in self.roots:
            if not root.is_dir() or root.is_symlink():
                continue
            for directory in sorted(path for path in root.iterdir() if path.is_dir()):
                try:
                    manifest = self._manifest(directory)
                except Exception as exc:
                    self.errors[directory.name] = str(exc)
                    continue
                found[manifest.name] = (manifest, directory)
        return found

    def load(self):
        for name, (manifest, directory) in self.discover().items():
            if name not in self.enabled:
                continue
            try:
                module = self._module(directory, name)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise ValueError("plugin register(context) is required")
                context = PluginContext(
                    name,
                    self.tools,
                    self.hooks,
                    allow_override=name in self.allow_overrides,
                )
                result = register(context)
                if inspect.isawaitable(result):
                    raise ValueError("async plugin registration is not supported")
                self.loaded[name] = LoadedPlugin(
                    manifest,
                    module,
                    context.registered_tools,
                    context.registered_hooks,
                )
            except Exception as exc:
                self.errors[name] = str(exc)
        return self.loaded

    def invoke(self, hook, **kwargs):
        if hook not in VALID_HOOKS:
            raise ValueError("invalid plugin hook")
        results = []
        for callback in self.hooks.get(hook, []):
            try:
                value = callback(**kwargs)
                if inspect.isawaitable(value):
                    raise ValueError("async hooks are not supported")
                results.append(value)
            except Exception as exc:
                results.append({"error": str(exc)})
        return results
