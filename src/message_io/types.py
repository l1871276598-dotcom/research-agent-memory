"""Normalized message types selectively adapted from Hermes platform adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MessageSource:
    platform: str
    user_id: str
    channel_id: str
    thread_id: str | None = None
    chat_type: str = "dm"

    def __post_init__(self):
        for name in ("platform", "user_id", "channel_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    @property
    def session_key(self):
        parts = [self.platform, self.user_id, self.channel_id]
        if self.thread_id:
            parts.append(self.thread_id)
        return ":".join(parts)


@dataclass(frozen=True)
class MessageEvent:
    message_id: str
    text: str
    source: MessageSource
    attachments: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            raise ValueError("message_id must be a non-empty string")
        if not isinstance(self.text, str):
            raise ValueError("message text must be a string")
        if not isinstance(self.source, MessageSource):
            raise ValueError("message source is invalid")


def utf16_len(value):
    return len(str(value).encode("utf-16-le")) // 2


def _prefix(value, limit):
    if utf16_len(value) <= limit:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if utf16_len(value[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def split_message(value, limit=4000):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("message limit must be a positive integer")
    remaining = str(value)
    parts = []
    while remaining:
        chunk = _prefix(remaining, limit)
        if not chunk:
            raise ValueError("message cannot be split")
        if len(chunk) < len(remaining):
            boundary = max(chunk.rfind("\n"), chunk.rfind(" "))
            if boundary > len(chunk) // 2:
                chunk = chunk[:boundary]
        parts.append(chunk)
        remaining = remaining[len(chunk) :].lstrip()
    return parts or [""]
