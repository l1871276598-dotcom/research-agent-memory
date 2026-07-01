from __future__ import annotations

from abc import ABC, abstractmethod

from .types import MessageEvent, MessageSource, split_message


class BaseAdapter(ABC):
    platform = ""
    message_limit = 4000

    @abstractmethod
    def start(self, on_event):
        raise NotImplementedError

    @abstractmethod
    def stop(self):
        raise NotImplementedError

    @abstractmethod
    def send(self, source: MessageSource, text: str, *, reply_to=None, metadata=None):
        raise NotImplementedError

    def send_text(self, source, text, *, reply_to=None, metadata=None):
        results = []
        for part in split_message(text, self.message_limit):
            results.append(
                self.send(source, part, reply_to=reply_to, metadata=dict(metadata or {}))
            )
            reply_to = None
        return results


class CallbackAdapter(BaseAdapter):
    """Dependency-free adapter for webhooks and extension-backed platforms."""

    def __init__(self, platform, sender, *, message_limit=4000):
        if not isinstance(platform, str) or not platform.strip() or not callable(sender):
            raise ValueError("invalid callback adapter")
        self.platform = platform.strip().lower()
        self.sender = sender
        self.message_limit = message_limit
        self.on_event = None

    def start(self, on_event):
        if not callable(on_event):
            raise ValueError("on_event must be callable")
        self.on_event = on_event
        return self

    def receive(self, event):
        if self.on_event is None:
            raise RuntimeError("adapter is not started")
        if not isinstance(event, MessageEvent):
            raise ValueError("event is invalid")
        return self.on_event(event)

    def stop(self):
        self.on_event = None

    def send(self, source, text, *, reply_to=None, metadata=None):
        return self.sender(
            source=source,
            text=text,
            reply_to=reply_to,
            metadata=dict(metadata or {}),
        )


class AdapterRegistry:
    def __init__(self):
        self.adapters = {}

    def register(self, adapter, *, allow_override=False):
        platform = getattr(adapter, "platform", None)
        if not isinstance(platform, str) or not platform.strip():
            raise ValueError("adapter platform is invalid")
        if platform in self.adapters and not allow_override:
            raise ValueError("platform adapter already registered")
        for method in ("start", "stop", "send_text"):
            if not callable(getattr(adapter, method, None)):
                raise ValueError("platform adapter is invalid")
        self.adapters[platform] = adapter
        return adapter

    def get(self, platform):
        try:
            return self.adapters[platform]
        except KeyError as exc:
            raise ValueError("unknown platform adapter") from exc

    def start_all(self, on_event):
        return {name: adapter.start(on_event) for name, adapter in self.adapters.items()}

    def stop_all(self):
        for adapter in self.adapters.values():
            adapter.stop()
