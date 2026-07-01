"""Reference-only conversation compaction adapted from Hermes context handling."""

import json


PREFIX = "[HISTORICAL CONTEXT — REFERENCE ONLY]"
SUFFIX = "--- END HISTORICAL CONTEXT ---"


def estimate_tokens(message):
    content = message.get("content", "")
    size = len(content) if isinstance(content, str) else len(str(content))
    calls = message.get("tool_calls", []) or []
    return size // 4 + 10 + sum(len(json.dumps(call, default=str)) // 4 for call in calls)


def _summary(messages, limit=8000):
    lines = []
    used = 0
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        text = content if isinstance(content, str) else str(content)
        text = text.replace("\n", " ").strip()
        if role not in {"user", "assistant", "tool"} or not text:
            continue
        line = f"{str(role).upper()}: {text[:700]}"
        lines.append(line)
        used += len(line)
        if used >= limit:
            break
    return "\n".join(lines)[:limit]


class ContextCompactor:
    def __init__(self, summarizer=None):
        if summarizer is not None and not callable(summarizer):
            raise ValueError("summarizer must be callable")
        self.summarizer = summarizer

    def compact(self, messages, *, max_tokens=12000, tail_tokens=5000):
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise ValueError("messages must be a list of objects")
        before = sum(estimate_tokens(item) for item in messages)
        if before <= max_tokens or len(messages) < 4:
            return {"compressed": False, "messages": list(messages)}

        head = [dict(messages[0])] if messages[0].get("role") == "system" else []
        start = len(head)
        tail = []
        used = 0
        for item in reversed(messages[start:]):
            cost = estimate_tokens(item)
            if tail and used + cost > tail_tokens:
                break
            tail.append(dict(item))
            used += cost
        tail.reverse()
        while tail and tail[0].get("role") == "tool":
            index = len(messages) - len(tail) - 1
            if index < start:
                break
            tail.insert(0, dict(messages[index]))
        middle = messages[start : len(messages) - len(tail)]
        if not middle:
            return {"compressed": False, "messages": list(messages)}

        summary = ""
        if self.summarizer is not None:
            try:
                summary = str(self.summarizer(list(middle))).strip()
            except Exception:
                summary = ""
        summary = summary or _summary(middle)
        compacted = head + [
            {
                "role": "assistant",
                "content": f"{PREFIX}\n{summary}\n{SUFFIX}",
                "_compressed_summary": True,
            }
        ] + tail
        return {
            "compressed": True,
            "messages": compacted,
            "dropped_messages": len(middle),
            "tokens_before": before,
            "tokens_after": sum(estimate_tokens(item) for item in compacted),
        }
