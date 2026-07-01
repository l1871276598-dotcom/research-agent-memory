"""Unified LAOS tool registry inspired by Hermes Agent's tool registry."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: object
    mutating: bool = False


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(
        self,
        name,
        handler,
        *,
        description="",
        parameters=None,
        mutating=False,
        allow_override=False,
    ):
        if not isinstance(name, str) or not name.strip() or not callable(handler):
            raise ValueError("invalid tool registration")
        if name in self._tools and not allow_override:
            raise ValueError(f"tool already registered: {name}")
        schema = parameters or {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            raise ValueError("tool parameters must be an object")
        spec = ToolSpec(
            name=name,
            description=str(description),
            parameters=schema,
            handler=handler,
            mutating=bool(mutating),
        )
        self._tools[name] = spec
        return spec

    def get(self, name):
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {name}") from exc

    def names(self):
        return sorted(self._tools)

    def definitions(self, allowed=None):
        allowed = set(allowed) if allowed is not None else None
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
            if allowed is None or spec.name in allowed
        ]

    def call(self, name, arguments=None):
        spec = self.get(name)
        values = arguments or {}
        if not isinstance(values, dict):
            raise ValueError("tool arguments must be an object")
        return spec.handler(**values)
