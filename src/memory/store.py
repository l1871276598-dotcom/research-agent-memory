import copy
import re

from . import WORKSPACE_CHOICES, collect_validated_records


_TERMS = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORKSPACE_CHOICES = frozenset(WORKSPACE_CHOICES)


def _optional_text(value, name):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value


class MemoryStore:
    def __init__(self, root):
        self.root, _ = self._load(root)

    @staticmethod
    def _load(root):
        validated_root, rows, errors, _ = collect_validated_records(root)
        if errors:
            raise ValueError("memory store validation failed")
        return validated_root, rows

    def records(self):
        _, rows = self._load(self.root)
        records = []
        for item in rows:
            record = copy.deepcopy(item["record"])
            if record.get("status") == "conflict":
                record["status"] = "conflicted"
            record["relative_path"] = item["relative_path"]
            records.append(record)
        return sorted(records, key=lambda record: record["id"])

    def get(self, memory_id):
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory id must be a non-empty string")
        for record in self.records():
            if record["id"] == memory_id:
                return record
        raise ValueError("memory not found")

    def active_relevant(self, query, workspace=None, project=None):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(workspace, str) or workspace not in _WORKSPACE_CHOICES:
            raise ValueError("workspace must be personal or work")
        project = _optional_text(project, "project")
        terms = {term.casefold() for term in _TERMS.findall(query)}
        ranked = []
        for record in self.records():
            if record.get("status") != "active" or record.get("confidentiality") == "restricted":
                continue
            if record.get("workspace") != workspace:
                continue
            record_project = record.get("project")
            if project is None:
                if record_project is not None:
                    continue
            elif record_project not in (None, project):
                continue
            values = []
            for field in ("title", "content", "tags"):
                value = record.get(field, "")
                values.extend(value if isinstance(value, list) else [value])
            haystack = " ".join(str(value) for value in values).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, record["id"], record))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [record for _, _, record in ranked]
