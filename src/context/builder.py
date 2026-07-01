import re


_WHITESPACE = re.compile(r"\s+")


class ContextBuilder:
    def __init__(self, store):
        if not callable(getattr(store, "active_relevant", None)):
            raise ValueError("context store is invalid")
        self.store = store

    @staticmethod
    def _normalize(value):
        return _WHITESPACE.sub(" ", str(value)).strip()

    @classmethod
    def _render(cls, entry):
        if not isinstance(entry, dict):
            raise ValueError("context entry must be a mapping")
        memory_id = entry.get("id")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("context entry id must be a non-empty string")
        body = [
            f"title: {cls._normalize(entry.get('title', ''))}",
            f"content: {cls._normalize(entry.get('content', ''))}",
        ]
        tags = entry.get("tags")
        if tags:
            body.append("tags: " + ", ".join(cls._normalize(tag) for tag in tags))
        return cls._normalize(memory_id), "\n".join(body)

    @staticmethod
    def empty(limit=8000):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("context limit must be a positive integer")
        return {"text": "", "sources": [], "limit": limit, "used": 0}

    def build(self, query, limit=8000, workspace=None, project=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("context limit must be a positive integer")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        entries = self.store.active_relevant(query, workspace, project)
        text = ""
        sources = []
        seen_ids = set()
        seen_pieces = set()
        for entry in entries:
            entry_id, body = self._render(entry)
            if entry_id in seen_ids or body in seen_pieces:
                continue
            piece = f"id: {entry_id}\n{body}"
            separator = "\n\n" if text else ""
            remaining = limit - len(text)
            if remaining <= len(separator):
                break
            fitted = piece[: remaining - len(separator)]
            if not fitted:
                break
            text += separator + fitted
            sources.append(entry_id)
            seen_ids.add(entry_id)
            seen_pieces.add(body)
            if len(fitted) < len(piece):
                break
        return {"text": text, "sources": sources, "limit": limit, "used": len(text)}
