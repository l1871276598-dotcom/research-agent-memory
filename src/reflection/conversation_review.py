"""LAOS-native conversation review inspired by Hermes Agent.

Selected review/digest behavior is adapted from
NousResearch/hermes-agent `agent/background_review.py` (MIT License).
LAOS changes the persistence boundary: review output can only create
candidates and can never write active memory directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from memory.candidate import validate_candidate_values
from review.source_refs import publish_source_artifact


MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and identify only durable information worth "
    "remembering. Focus on user preferences, stable personal or project facts, "
    "explicit corrections, lasting decisions, reusable procedures, and durable "
    "expectations about how the agent should work. Treat assistant messages as "
    "generated context, not evidence about the user. A user fact or preference must "
    "be supported by a user message, an explicit user confirmation, a provided file, "
    "or a tool result. Never promote an assistant-only assertion, inference, or guess "
    "into user memory. Do not save one-off requests, temporary failures, resolved "
    "setup problems, or unsupported assumptions. Put durable proposals in "
    "memory_candidates and include source_refs when evidence is available."
)

SKILL_REVIEW_PROMPT = (
    "Identify reusable ways of doing a class of task: workflow corrections, "
    "non-trivial techniques, fixes, verification patterns, or durable style rules. "
    "A proposed procedure must be grounded in a user-approved workflow, successful "
    "tool evidence, or an explicit correction; an assistant suggestion alone is not "
    "sufficient evidence. Do not create a skill for a single task narrative, a "
    "transient environment failure, or a negative claim that a tool is permanently "
    "broken. Put these proposals in skill_candidates; LAOS will review them separately."
)

RESPONSE_SCHEMA_PROMPT = (
    "Return JSON only with this exact top-level shape:\n"
    '{"memory_candidates":[],"skill_candidates":[],"nothing_to_save":true}\n\n'
    "Each memory candidate must match the LAOS candidate schema and may contain: "
    "type, title, scope, workspace, confidentiality, source, content, action, "
    "confidence, target_id, project, context_id, source_id, evidence, source_refs, "
    "relations, tags, and status. Never mark anything active. Set nothing_to_save "
    "to true only when both candidate lists are empty."
)


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text", ""), str)
        ).strip()
    return ""


def _validated_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    output = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("messages must contain objects")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("message role is invalid")
        output.append(dict(message))
    return output


def digest_history(messages: list[dict[str, Any]], tail: int = 24) -> list[dict[str, Any]]:
    """Keep recent messages and collapse older turns into a compact digest.

    This is a selective port of Hermes' routed-review digest. A leading tool
    message is never left detached from the assistant call that produced it.
    """
    if isinstance(tail, bool) or not isinstance(tail, int) or tail <= 0:
        raise ValueError("tail must be a positive integer")
    items = _validated_messages(messages)
    if len(items) <= tail:
        return items

    keep = items[-tail:]
    while keep and keep[0].get("role") == "tool":
        tail += 1
        if len(items) <= tail:
            return items
        keep = items[-tail:]

    lines: list[str] = []
    for message in items[: -len(keep)]:
        role = message.get("role")
        text = _message_text(message).replace("\n", " ")
        if role == "user" and text:
            lines.append(f"USER: {text[:300]}")
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            if isinstance(calls, list) and calls:
                names = []
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    names.append(function.get("name", "?") if isinstance(function, dict) else "?")
                if names:
                    lines.append(f"ASSISTANT[tools: {', '.join(names)}]")
            if text:
                lines.append(f"ASSISTANT: {text[:200]}")

    digest = {
        "role": "user",
        "content": (
            "[Earlier conversation digest — older turns were summarised to bound "
            "review context. Recent turns follow verbatim.]\n" + "\n".join(lines)
        ),
    }
    return [digest] + keep


def prepare_review_messages(
    messages: list[dict[str, Any]],
    *,
    review_memory: bool = True,
    review_skills: bool = False,
    routed: bool = False,
    tail: int = 24,
) -> list[dict[str, Any]]:
    if not review_memory and not review_skills:
        raise ValueError("at least one review dimension must be enabled")
    history = digest_history(messages, tail) if routed else _validated_messages(messages)
    prompts = []
    if review_memory:
        prompts.append(MEMORY_REVIEW_PROMPT)
    else:
        prompts.append("Memory review is disabled; memory_candidates must be empty.")
    if review_skills:
        prompts.append(SKILL_REVIEW_PROMPT)
    else:
        prompts.append("Skill review is disabled; skill_candidates must be empty.")
    prompts.append(RESPONSE_SCHEMA_PROMPT)
    return history + [{"role": "user", "content": "\n\n".join(prompts)}]


def parse_review_response(response: Any) -> dict[str, Any]:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError("review response must be valid JSON") from exc
    if not isinstance(response, dict):
        raise ValueError("review response must be an object")
    allowed = {"memory_candidates", "skill_candidates", "nothing_to_save"}
    if set(response) != allowed:
        raise ValueError("review response has an invalid schema")
    memories = response["memory_candidates"]
    skills = response["skill_candidates"]
    nothing = response["nothing_to_save"]
    if not isinstance(memories, list) or not all(isinstance(item, dict) for item in memories):
        raise ValueError("memory_candidates must be a list of objects")
    if not isinstance(skills, list) or not all(isinstance(item, dict) for item in skills):
        raise ValueError("skill_candidates must be a list of objects")
    if not isinstance(nothing, bool):
        raise ValueError("nothing_to_save must be boolean")
    if nothing and (memories or skills):
        raise ValueError("nothing_to_save conflicts with proposed changes")
    if not nothing and not memories and not skills:
        raise ValueError("empty review must set nothing_to_save")
    return {
        "memory_candidates": [dict(item) for item in memories],
        "skill_candidates": [dict(item) for item in skills],
        "nothing_to_save": nothing,
    }


class ReviewCounter:
    """Threshold state copied from Hermes' memory/skill nudge behavior."""

    def __init__(self, memory_interval: int = 10, skill_interval: int = 10):
        for value in (memory_interval, skill_interval):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("review intervals must be positive integers")
        self.memory_interval = memory_interval
        self.skill_interval = skill_interval
        self.turns_since_memory = 0
        self.iterations_since_skill = 0

    def record_turn(self, tool_iterations: int = 0) -> dict:
        if (
            isinstance(tool_iterations, bool)
            or not isinstance(tool_iterations, int)
            or tool_iterations < 0
        ):
            raise ValueError("tool_iterations must be a non-negative integer")
        self.turns_since_memory += 1
        self.iterations_since_skill += tool_iterations
        memory_due = self.turns_since_memory >= self.memory_interval
        skill_due = self.iterations_since_skill >= self.skill_interval
        if memory_due:
            self.turns_since_memory = 0
        if skill_due:
            self.iterations_since_skill = 0
        return {"review_memory": memory_due, "review_skills": skill_due}


class ConversationReviewService:
    """Prepare model review and persist only LAOS-owned candidates."""

    def __init__(
        self,
        memory_core: Any,
        reviewer: Callable | None = None,
        state_dir: str | Path | None = None,
    ):
        if not callable(getattr(memory_core, "create_candidate", None)):
            raise ValueError("memory core is invalid")
        if reviewer is not None and not callable(reviewer):
            raise ValueError("reviewer must be callable")
        if state_dir is None:
            state_dir = getattr(getattr(memory_core, "candidates", None), "state_dir", None)
        self.state_dir = None if state_dir is None else Path(state_dir).expanduser()
        self.memory_core = memory_core
        self.reviewer = reviewer

    def prepare(self, messages: list[dict[str, Any]], **options: Any) -> list[dict[str, Any]]:
        return prepare_review_messages(messages, **options)

    def apply(
        self,
        response: Any,
        *,
        workspace: str,
        confidentiality: str = "personal",
        project: str | None = None,
        session_id: str | None = None,
        review_id: str | None = None,
    ) -> dict:
        parsed = parse_review_response(response)
        candidate_ids = []
        results = []
        finder = getattr(self.memory_core, "find_candidate_by_source_id", None)
        for index, item in enumerate(parsed["memory_candidates"]):
            value = dict(item)
            value.update(
                {
                    "workspace": workspace,
                    "confidentiality": confidentiality,
                    "source": "agent:conversation_review",
                    "confidence": "inferred",
                    "action": "create",
                    "status": "candidate",
                }
            )
            value.pop("target_id", None)
            if project is None:
                value.pop("project", None)
            else:
                value["project"] = project
            if review_id:
                value["source_id"] = f"review:{review_id}:memory:{index}"
            existing = None
            if review_id and callable(finder):
                existing = finder(value["source_id"])
            if existing is not None:
                result = existing
            else:
                if self.state_dir is None:
                    raise ValueError("conversation review source state is unavailable")
                refs = list(value.get("source_refs") or [])
                artifact_ref = publish_source_artifact(
                    self.state_dir,
                    kind="conversation_review_evidence",
                    workspace=workspace,
                    project=project,
                    confidentiality=confidentiality,
                    payload={
                        "session_id": session_id,
                        "review_id": review_id,
                        "candidate_index": index,
                        "candidate": item,
                    },
                )
                if artifact_ref not in refs:
                    refs.append(artifact_ref)
                value["source_refs"] = refs
                validate_candidate_values(value)
                result = self.memory_core.create_candidate(value)
            results.append(result)
            candidate_ids.append(result["candidate_id"])
        return {
            "candidate_ids": candidate_ids,
            "candidate_results": results,
            "skill_candidates": parsed["skill_candidates"],
            "nothing_to_save": parsed["nothing_to_save"],
        }

    def review(
        self,
        messages: list[dict[str, Any]],
        *,
        workspace: str,
        confidentiality: str = "personal",
        project: str | None = None,
        session_id: str | None = None,
        review_memory: bool = True,
        review_skills: bool = False,
        routed: bool = False,
        tail: int = 24,
        review_id: str | None = None,
    ) -> dict:
        if self.reviewer is None:
            raise RuntimeError("conversation reviewer is not configured")
        prepared = self.prepare(
            messages,
            review_memory=review_memory,
            review_skills=review_skills,
            routed=routed,
            tail=tail,
        )
        response = self.reviewer(prepared)
        return self.apply(
            response,
            workspace=workspace,
            confidentiality=confidentiality,
            project=project,
            session_id=session_id,
            review_id=review_id,
        )
