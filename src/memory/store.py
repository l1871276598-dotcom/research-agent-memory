import copy
import re

from . import WORKSPACE_CHOICES, collect_validated_records


_TERMS = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_WORKSPACE_CHOICES = frozenset(WORKSPACE_CHOICES)
_REVIEWABLE_STATUSES = frozenset({"candidate", "conflicted"})
_CONFIDENTIALITY_LEVELS = ["public", "personal", "internal", "restricted"]


def _confidentiality_rank(value):
    if value is None:
        return None
    if not isinstance(value, str) or value not in _CONFIDENTIALITY_LEVELS:
        raise ValueError("confidentiality must be public, personal, internal or restricted")
    return _CONFIDENTIALITY_LEVELS.index(value)


def _optional_text(value, name):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _workspace(value):
    if not isinstance(value, str) or value not in _WORKSPACE_CHOICES:
        raise ValueError("workspace must be personal or work")
    return value


def _statuses(values):
    if values is None:
        return _REVIEWABLE_STATUSES
    if not isinstance(values, list) or not values:
        raise ValueError("statuses must be a non-empty list")
    normalized = set()
    for value in values:
        if value == "conflict":
            value = "conflicted"
        if value not in _REVIEWABLE_STATUSES:
            raise ValueError("statuses must be candidate or conflicted")
        normalized.add(value)
    return frozenset(normalized)


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

    def reviewable(self, workspace=None, project=None, statuses=None):
        workspace = _workspace(workspace)
        project = _optional_text(project, "project")
        allowed_statuses = _statuses(statuses)
        results = []
        for record in self.records():
            if record.get("status") not in allowed_statuses:
                continue
            if record.get("workspace") != workspace:
                continue
            if record.get("confidentiality") == "restricted":
                continue
            if project is not None and record.get("project") != project:
                continue
            results.append(record)
        return results

    def active_relevant(self, query, workspace=None, project=None, confidentiality=None):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        workspace = _workspace(workspace)
        ceiling_rank = _confidentiality_rank(confidentiality)
        project = _optional_text(project, "project")
        terms = {term.casefold() for term in _TERMS.findall(query)}
        ranked = []
        for record in self.records():
            if record.get("status") != "active" or record.get("confidentiality") == "restricted":
                continue
            # GP9-02: a caller-confidentiality ceiling filters records strictly.
            # When a ceiling is supplied, records more confidential than the
            # ceiling are excluded (public < personal < internal < restricted).
            if ceiling_rank is not None:
                record_conf = record.get("confidentiality")
                if record_conf not in _CONFIDENTIALITY_LEVELS or _CONFIDENTIALITY_LEVELS.index(record_conf) > ceiling_rank:
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
