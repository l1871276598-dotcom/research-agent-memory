import re
from pathlib import Path


_TERMS = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


def _words(value):
    return {item.casefold() for item in _TERMS.findall(str(value))}


class ProcedureContextBuilder:
    def __init__(self, data_root):
        self.root = Path(data_root).expanduser().resolve(strict=False) / "procedures"

    def build(self, query, limit=6000):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        terms = _words(query)
        rows = []
        if not terms or not self.root.is_dir():
            return {"text": "", "sources": [], "truncated": False}
        for document in self.root.glob("*/PROCEDURE.md"):
            if document.is_symlink() or document.parent.is_symlink() or not document.is_file():
                continue
            content = document.read_text(encoding="utf-8")
            score = len(terms & _words(document.parent.name + " " + content[:5000]))
            if score:
                rows.append((score, document.parent.name, content))
        rows.sort(key=lambda row: (-row[0], row[1]))

        parts = []
        sources = []
        used = 0
        truncated = False
        for _, name, content in rows:
            block = f"procedure: {name}\n{content.strip()}"
            remaining = limit - used
            if remaining <= 0:
                truncated = True
                break
            if len(block) > remaining:
                parts.append(block[:remaining])
                sources.append(name)
                truncated = True
                break
            parts.append(block)
            sources.append(name)
            used += len(block)
        return {"text": "\n\n".join(parts), "sources": sources, "truncated": truncated}
