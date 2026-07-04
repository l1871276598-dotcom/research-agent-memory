import hashlib
import json
import re
from pathlib import Path

import memory

from .base import BaseAgent
from .reflection import (
    REFLECTION_CONTRACT_ID,
    REFLECTION_FILENAME,
    UNKNOWN,
    _reflection_output,
    _run_id,
)


POLICY_CONTRACT_ID = "laos.policy-candidates.v1"
POLICY_FILENAME = "policy_candidates.json"
POLICY_REVIEW_FILENAME = "policy_review.md"
RULE_HEADING_PATTERN = re.compile(r"^## rule-([0-9a-f]{16})$")
RULE_POLICY_PATTERN = re.compile(r"^- Policy: (.+)$")
REQUIRE_PATTERN = re.compile(r"^require\s*:\s*(.+)$", re.IGNORECASE)
DENY_PATTERN = re.compile(r"^forbid\s*:\s*(.+)$", re.IGNORECASE)
REQUIRE_ZH_PATTERN = re.compile(r"^(?:必须|要求)\s*[:：]\s*(.+)$")
DENY_ZH_PATTERN = re.compile(r"^禁止\s*[:：]\s*(.+)$")
REFLECTION_ARTIFACT_KEYS = {
    "schema_version",
    "contract_id",
    "run_id",
    "source_reflection_sha256",
    "reflection",
}


def _normalized(value):
    if not isinstance(value, str):
        raise ValueError("policy text must be a string")
    return " ".join(value.split())


def _policy_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _directive(value):
    for effect, pattern in (
        ("require", REQUIRE_PATTERN),
        ("deny", DENY_PATTERN),
        ("require", REQUIRE_ZH_PATTERN),
        ("deny", DENY_ZH_PATTERN),
    ):
        match = pattern.fullmatch(value)
        if match is not None:
            subject = _normalized(match.group(1))
            if not subject:
                raise ValueError("policy directive subject is empty")
            return effect, subject.casefold()
    return "guidance", value.casefold()


def _approved_rules(root):
    path = Path(root) / "memory_rules.md"
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ValueError("memory rules path is unsafe")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("memory rules could not be read") from exc
    rules = []
    current_id = None
    for line in lines:
        heading = RULE_HEADING_PATTERN.fullmatch(line)
        if heading is not None:
            current_id = heading.group(1)
            continue
        policy = RULE_POLICY_PATTERN.fullmatch(line)
        if policy is not None and current_id is not None:
            text = _normalized(policy.group(1))
            if not text or current_id != _policy_id(text):
                raise ValueError("memory rules are invalid")
            effect, subject = _directive(text)
            rules.append(
                {
                    "rule_id": current_id,
                    "text": text,
                    "effect": effect,
                    "subject": subject,
                }
            )
            current_id = None
    return rules


def _load_inputs(root, state_dir, run_id):
    from memory_agent import LOOP_CONTRACT_ID, LOOP_CONTRACT_VERSION, _load_loop_run

    paths, run_data, _ = _load_loop_run(Path(state_dir), run_id)
    if not (
        run_data.get("contract_id") == LOOP_CONTRACT_ID
        and run_data.get("contract_version") == LOOP_CONTRACT_VERSION
    ):
        raise ValueError("policy learning requires a v2 Loop run")
    reflection_path = paths["run"].parent / REFLECTION_FILENAME
    if reflection_path.is_symlink() or not reflection_path.is_file():
        raise ValueError("reflection result is required")
    try:
        reflection_bytes = reflection_path.read_bytes()
        reflection_artifact = json.loads(reflection_bytes.decode("utf-8"))
        reflection_text = paths["reflection"].read_text(encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("reflection result is invalid") from exc
    if not isinstance(reflection_artifact, dict):
        raise ValueError("reflection result is invalid")
    reflection = reflection_artifact.get("reflection")
    if (
        set(reflection_artifact) != REFLECTION_ARTIFACT_KEYS
        or reflection_artifact.get("schema_version") != 1
        or reflection_artifact.get("contract_id") != REFLECTION_CONTRACT_ID
        or reflection_artifact.get("run_id") != run_id
        or reflection_artifact.get("source_reflection_sha256")
        != hashlib.sha256(reflection_text.encode("utf-8")).hexdigest()
        or reflection != _reflection_output(run_data, reflection_text)
    ):
        raise ValueError("reflection result is invalid")
    return paths, run_data, reflection_bytes, reflection


def _candidate_texts(reflection):
    lesson = reflection.get("reusable_lesson")
    if lesson == UNKNOWN:
        return []
    normalized = _normalized(lesson)
    return [normalized] if normalized else []


def _candidates(texts, approved):
    approved_by_id = {item["rule_id"]: item for item in approved}
    opposite = {"require": "deny", "deny": "require"}
    candidates = []
    seen = set()
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        policy_id = _policy_id(text)
        effect, subject = _directive(text)
        duplicate_of = policy_id if policy_id in approved_by_id else None
        conflicts = sorted(
            item["rule_id"]
            for item in approved
            if effect in opposite
            and item["effect"] == opposite[effect]
            and item["subject"] == subject
        )
        status = (
            "duplicate"
            if duplicate_of
            else "conflicted"
            if conflicts
            else "review_required"
        )
        candidates.append(
            {
                "policy_id": policy_id,
                "text": text,
                "normalized_text": text,
                "effect": effect,
                "subject": subject,
                "status": status,
                "duplicate_of": duplicate_of,
                "conflicts_with": conflicts,
            }
        )
    return candidates


def _review_text(candidates):
    sections = ["# Policy Review", "", "## Reviewable", ""]
    reviewable = [item for item in candidates if item["status"] == "review_required"]
    if reviewable:
        sections.extend(
            f'- [ ] {item["text"]} <!-- policy_id: {item["policy_id"]} -->'
            for item in reviewable
        )
    else:
        sections.append("No reviewable policy candidates.")
    sections.extend(["", "## Duplicates", ""])
    duplicates = [item for item in candidates if item["status"] == "duplicate"]
    if duplicates:
        sections.extend(
            f'- duplicate: {item["text"]} <!-- policy_id: {item["policy_id"]} -->'
            for item in duplicates
        )
    else:
        sections.append("None.")
    sections.extend(["", "## Conflicts", ""])
    conflicted = [item for item in candidates if item["status"] == "conflicted"]
    if conflicted:
        sections.extend(
            f'- conflicted: {item["text"]} <!-- policy_id: {item["policy_id"]}; conflicts_with: {",".join(item["conflicts_with"])} -->'
            for item in conflicted
        )
    else:
        sections.append("None.")
    return "\n".join(sections) + "\n"


def _artifact_payload(run_id, reflection_bytes, candidates):
    return {
        "schema_version": 1,
        "contract_id": POLICY_CONTRACT_ID,
        "run_id": run_id,
        "source_reflection_artifact_sha256": hashlib.sha256(reflection_bytes).hexdigest(),
        "candidates": candidates,
        "review_required": any(
            item["status"] == "review_required" for item in candidates
        ),
        "applied": False,
    }


def _publish(state_dir, paths, run_data, payload, review_text):
    run_dir = paths["run"].parent
    artifact_path = run_dir / POLICY_FILENAME
    review_path = run_dir / POLICY_REVIEW_FILENAME
    updated_run = dict(run_data)
    candidate_ids = [item["policy_id"] for item in payload["candidates"]]
    updated_run["policy_suggestion_ids"] = candidate_ids
    artifact_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    run_text = json.dumps(updated_run, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    artifact_matches = False
    review_matches = False
    if artifact_path.exists() or review_path.exists():
        if (
            artifact_path.is_symlink()
            or review_path.is_symlink()
            or not artifact_path.is_file()
            or not review_path.is_file()
        ):
            raise ValueError("policy artifact path is unsafe")
        try:
            artifact_matches = artifact_path.read_text(encoding="utf-8") == artifact_text
            review_matches = review_path.read_text(encoding="utf-8") == review_text
        except (OSError, UnicodeError) as exc:
            raise ValueError("policy artifact is invalid") from exc
        if not artifact_matches or not review_matches:
            raise ValueError("policy artifact conflicts with current evidence")
    run_matches = run_data.get("policy_suggestion_ids") == candidate_ids
    operations = []
    if not artifact_matches:
        operations.extend([(artifact_path, artifact_text), (review_path, review_text)])
    if not run_matches:
        operations.append((paths["run"], run_text))
    if operations:
        memory._replace_transaction(operations, [])
    state = Path(state_dir).expanduser()
    try:
        artifact = artifact_path.relative_to(state).as_posix()
        review = review_path.relative_to(state).as_posix()
    except ValueError as exc:
        raise ValueError("policy artifact escaped the state directory") from exc
    return artifact, review, not operations


class PolicyAgent(BaseAgent):
    agent_id = "policy_agent"
    handles = ["loop.suggest-policies"]

    def __init__(self, root, state_dir):
        if isinstance(root, bool) or isinstance(state_dir, bool):
            raise ValueError("policy paths must be filesystem paths")
        try:
            self.root = Path(root).expanduser()
            self.state_dir = Path(state_dir).expanduser()
        except TypeError as exc:
            raise ValueError("policy paths must be filesystem paths") from exc

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")
        values = task.get("input")
        if not isinstance(values, dict) or set(values) != {"run_id"}:
            raise ValueError("task.input must contain only run_id")
        run_id = _run_id(values.get("run_id"))
        paths, run_data, reflection_bytes, reflection = _load_inputs(
            self.root, self.state_dir, run_id
        )
        candidates = _candidates(_candidate_texts(reflection), _approved_rules(self.root))
        payload = _artifact_payload(run_id, reflection_bytes, candidates)
        artifact, review_artifact, reused = _publish(
            self.state_dir,
            paths,
            run_data,
            payload,
            _review_text(candidates),
        )
        counts = {
            status: sum(item["status"] == status for item in candidates)
            for status in ("review_required", "duplicate", "conflicted")
        }
        output = {
            "run_id": run_id,
            "artifact": artifact,
            "review_artifact": review_artifact,
            "reused": reused,
            "candidate_count": len(candidates),
            "reviewable_count": counts["review_required"],
            "duplicate_count": counts["duplicate"],
            "conflicted_count": counts["conflicted"],
            "applied": False,
        }
        return self.result(task, output, candidates=[], confidence=1.0)


__all__ = ["PolicyAgent"]
