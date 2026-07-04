from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from memory import atomic_write_text

from .base import BaseAgent


UNKNOWN = "unknown"
PLACEHOLDER = "Pending human or agent review."
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
REFLECTION_CONTRACT_ID = "laos.reflection-result.v1"
REFLECTION_FILENAME = "reflection_result.json"
REQUIRED_SECTIONS = {
    "Task",
    "Outcome",
    "Result Evidence",
    "Error Evidence",
    "What Worked",
    "What Failed",
    "Root Cause",
    "Next Change",
}
OUTPUT_KEYS = {
    "run_id",
    "outcome",
    "what_happened",
    "what_worked",
    "what_failed",
    "likely_cause",
    "reusable_lesson",
    "evidence",
    "unknown_fields",
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _run_id(value):
    value = _text(value, "run_id")
    if RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("run_id must be 32 lowercase hexadecimal characters")
    return value


def _sections(text):
    if not isinstance(text, str):
        raise ValueError("reflection document must be text")
    sections = {}
    current = None
    values = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                if current in sections:
                    raise ValueError("reflection document has duplicate sections")
                sections[current] = "\n".join(values).strip()
            current = line[3:].strip()
            values = []
        elif current is not None:
            values.append(line)
    if current is not None:
        if current in sections:
            raise ValueError("reflection document has duplicate sections")
        sections[current] = "\n".join(values).strip()
    if set(sections) != REQUIRED_SECTIONS:
        raise ValueError("reflection document has an invalid structure")
    return sections


def _known(value):
    normalized = value.strip()
    if not normalized or normalized in {"None", PLACEHOLDER}:
        return None
    return normalized


def _reflection_output(run_data, reflection_text):
    if not isinstance(run_data, dict):
        raise ValueError("loop run must be an object")
    run_id = _run_id(run_data.get("run_id"))
    outcome = run_data.get("outcome")
    if outcome not in {"pass", "fail"}:
        raise ValueError("loop outcome must be pass or fail")
    sections = _sections(reflection_text)
    if sections["Outcome"] != outcome:
        raise ValueError("reflection outcome does not match loop run")

    task = _known(sections["Task"])
    result = _known(sections["Result Evidence"])
    error = _known(sections["Error Evidence"])
    worked = _known(sections["What Worked"])
    failed = _known(sections["What Failed"])
    cause = _known(sections["Root Cause"])
    lesson = _known(sections["Next Change"])

    if outcome == "pass":
        what_happened = result or UNKNOWN
    else:
        what_happened = error or result or UNKNOWN

    evidence = []
    for kind, value in (
        ("task", task),
        ("result", result),
        ("error", error),
        ("worked", worked),
        ("failed", failed),
        ("root_cause", cause),
        ("next_change", lesson),
    ):
        if value is not None:
            evidence.append({"kind": kind, "value": value})

    output = {
        "run_id": run_id,
        "outcome": outcome,
        "what_happened": what_happened,
        "what_worked": worked or UNKNOWN,
        "what_failed": failed or UNKNOWN,
        "likely_cause": cause or UNKNOWN,
        "reusable_lesson": lesson or UNKNOWN,
        "evidence": evidence,
        "unknown_fields": [],
    }
    output["unknown_fields"] = [
        name
        for name in (
            "what_happened",
            "what_worked",
            "what_failed",
            "likely_cause",
            "reusable_lesson",
        )
        if output[name] == UNKNOWN
    ]
    if set(output) != OUTPUT_KEYS:
        raise ValueError("reflection output has an invalid schema")
    return output


def _load_from_state(state_dir, run_id):
    try:
        from memory_agent import _load_loop_run

        paths, run_data, _ = _load_loop_run(Path(state_dir).expanduser(), run_id)
        reflection_text = paths["reflection"].read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("loop reflection could not be read") from exc
    return paths, run_data, reflection_text


def _artifact_payload(run_id, reflection_text, reflection):
    return {
        "schema_version": 1,
        "contract_id": REFLECTION_CONTRACT_ID,
        "run_id": run_id,
        "source_reflection_sha256": hashlib.sha256(
            reflection_text.encode("utf-8")
        ).hexdigest(),
        "reflection": reflection,
    }


def _publish_artifact(state_dir, run_path, payload):
    run_dir = run_path.parent
    artifact = run_dir / REFLECTION_FILENAME
    if artifact.exists():
        if artifact.is_symlink() or not artifact.is_file():
            raise ValueError("reflection artifact path is unsafe")
        try:
            existing = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("reflection artifact is invalid") from exc
        if existing != payload:
            raise ValueError("reflection artifact conflicts with current evidence")
        reused = True
    else:
        atomic_write_text(
            artifact,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        reused = False
    try:
        relative = artifact.relative_to(Path(state_dir).expanduser()).as_posix()
    except ValueError as exc:
        raise ValueError("reflection artifact escaped the state directory") from exc
    return relative, reused


class ReflectionAgent(BaseAgent):
    agent_id = "reflection_agent"
    handles = ["loop.reflect"]

    def __init__(self, state_dir, loader=None):
        if isinstance(state_dir, bool):
            raise ValueError("state_dir must be a filesystem path")
        try:
            self.state_dir = Path(state_dir).expanduser()
        except TypeError as exc:
            raise ValueError("state_dir must be a filesystem path") from exc
        self.loader = loader or _load_from_state
        if not callable(self.loader):
            raise ValueError("reflection loader must be callable")

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")
        values = task.get("input")
        if not isinstance(values, dict) or set(values) != {"run_id"}:
            raise ValueError("task.input must contain only run_id")
        run_id = _run_id(values.get("run_id"))
        paths, run_data, reflection_text = self.loader(self.state_dir, run_id)
        reflection = _reflection_output(run_data, reflection_text)
        artifact_path, reused = _publish_artifact(
            self.state_dir,
            paths["run"],
            _artifact_payload(run_id, reflection_text, reflection),
        )
        output = {
            "run_id": run_id,
            "artifact": artifact_path,
            "reused": reused,
            "reflection": reflection,
            "applied": False,
        }
        return self.result(task, output, candidates=[], confidence=1.0)


class ConversationReviewAgent(BaseAgent):
    agent_id = "conversation_review_agent"
    handles = ["reflection.prepare", "reflection.apply"]

    def __init__(self, service):
        for name in ("prepare", "apply"):
            if not callable(getattr(service, name, None)):
                raise ValueError("conversation review service is invalid")
        self.service = service

    @staticmethod
    def _input(task, allowed):
        values = task.get("input")
        if not isinstance(values, dict) or not set(values) <= allowed:
            raise ValueError("task.input is invalid")
        return values

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")

        if task["type"] == "reflection.prepare":
            values = self._input(
                task,
                {"messages", "review_memory", "review_skills", "routed", "tail"},
            )
            messages = self.service.prepare(
                values.get("messages"),
                review_memory=values.get("review_memory", True),
                review_skills=values.get("review_skills", False),
                routed=values.get("routed", False),
                tail=values.get("tail", 24),
            )
            return self.result(task, {"messages": messages}, confidence=1.0)

        values = self._input(
            task,
            {"response", "workspace", "confidentiality", "project", "session_id"},
        )
        workspace = task.get("workspace", values.get("workspace"))
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace must be a non-empty string")
        output = self.service.apply(
            values.get("response"),
            workspace=workspace,
            confidentiality=values.get("confidentiality", "personal"),
            project=task.get("project", values.get("project")),
            session_id=values.get("session_id"),
        )
        return self.result(task, output, output["candidate_ids"], confidence=1.0)


__all__ = ["ConversationReviewAgent", "ReflectionAgent"]
