import hashlib
import json
import re
from pathlib import Path

from memory import atomic_write_text

from .base import BaseAgent
from .policy import (
    POLICY_CONTRACT_ID,
    POLICY_FILENAME,
    _directive,
    _normalized,
    _policy_id,
)
from .reflection import (
    REFLECTION_CONTRACT_ID,
    REFLECTION_FILENAME,
    _reflection_output,
)


CANDIDATE_GENERATION_CONTRACT_ID = "laos.low-risk-candidate.v1"
CANDIDATE_GENERATION_DIR = "generated_candidates"
MINIMUM_INDEPENDENT_EVIDENCE = 3
POLICY_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
MEMORY_REVIEW_STATES = {"candidate", "active", "archived", "conflicted"}
POLICY_ARTIFACT_KEYS = {
    "schema_version",
    "contract_id",
    "run_id",
    "source_reflection_artifact_sha256",
    "candidates",
    "review_required",
    "applied",
}
POLICY_CANDIDATE_KEYS = {
    "policy_id",
    "text",
    "normalized_text",
    "effect",
    "subject",
    "status",
    "duplicate_of",
    "conflicts_with",
}
REFLECTION_ARTIFACT_KEYS = {
    "schema_version",
    "contract_id",
    "run_id",
    "source_reflection_sha256",
    "reflection",
}


def _optional_text(value, name):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip() if isinstance(value, str) else None


def _request(policy_id, workspace, project):
    if not isinstance(policy_id, str) or POLICY_ID_PATTERN.fullmatch(policy_id) is None:
        raise ValueError("policy_id must be 16 lowercase hexadecimal characters")
    if workspace not in {"personal", "work"}:
        raise ValueError("workspace must be personal or work")
    project = _optional_text(project, "project")
    payload = {
        "policy_id": policy_id,
        "workspace": workspace,
        "project": project,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return payload, hashlib.sha256(encoded).hexdigest()[:32]


def _regular_json(path, message):
    if path.is_symlink() or not path.is_file():
        raise ValueError(message)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(message) from exc
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def _validated_policy_item(item):
    if not isinstance(item, dict) or set(item) != POLICY_CANDIDATE_KEYS:
        raise ValueError("policy candidate artifact is invalid")
    policy_id = item.get("policy_id")
    text = item.get("normalized_text")
    status = item.get("status")
    duplicate_of = item.get("duplicate_of")
    conflicts = item.get("conflicts_with")
    if (
        not isinstance(policy_id, str)
        or POLICY_ID_PATTERN.fullmatch(policy_id) is None
        or not isinstance(text, str)
        or not text
        or _normalized(text) != text
        or _policy_id(text) != policy_id
        or item.get("text") != text
        or status not in {"review_required", "duplicate", "conflicted"}
        or duplicate_of is not None
        and (
            not isinstance(duplicate_of, str)
            or POLICY_ID_PATTERN.fullmatch(duplicate_of) is None
        )
        or not isinstance(conflicts, list)
        or not all(
            isinstance(value, str) and POLICY_ID_PATTERN.fullmatch(value)
            for value in conflicts
        )
        or len(conflicts) != len(set(conflicts))
    ):
        raise ValueError("policy candidate artifact is invalid")
    effect, subject = _directive(text)
    if item.get("effect") != effect or item.get("subject") != subject:
        raise ValueError("policy candidate artifact is invalid")
    if status == "review_required" and (duplicate_of is not None or conflicts):
        raise ValueError("policy candidate artifact is invalid")
    if status == "duplicate" and (duplicate_of != policy_id or conflicts):
        raise ValueError("policy candidate artifact is invalid")
    if status == "conflicted" and (duplicate_of is not None or not conflicts):
        raise ValueError("policy candidate artifact is invalid")
    return item


def _validated_policy_artifact(run_dir, run_id, run_data):
    reflection_source_path = run_dir / "reflection.md"
    reflection_path = run_dir / REFLECTION_FILENAME
    policy_path = run_dir / POLICY_FILENAME
    if not policy_path.exists():
        return None
    artifact = _regular_json(policy_path, "policy candidate artifact is invalid")
    if (
        reflection_source_path.is_symlink()
        or not reflection_source_path.is_file()
        or reflection_path.is_symlink()
        or not reflection_path.is_file()
    ):
        raise ValueError("policy candidate source reflection is invalid")
    try:
        reflection_text = reflection_source_path.read_text(encoding="utf-8")
        reflection_bytes = reflection_path.read_bytes()
        reflection_artifact = json.loads(reflection_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("policy candidate source reflection is invalid") from exc
    if (
        not isinstance(reflection_artifact, dict)
        or set(reflection_artifact) != REFLECTION_ARTIFACT_KEYS
        or reflection_artifact.get("schema_version") != 1
        or reflection_artifact.get("contract_id") != REFLECTION_CONTRACT_ID
        or reflection_artifact.get("run_id") != run_id
        or reflection_artifact.get("source_reflection_sha256")
        != hashlib.sha256(reflection_text.encode("utf-8")).hexdigest()
        or reflection_artifact.get("reflection")
        != _reflection_output(run_data, reflection_text)
    ):
        raise ValueError("policy candidate source reflection is invalid")
    candidates = artifact.get("candidates")
    if (
        set(artifact) != POLICY_ARTIFACT_KEYS
        or artifact.get("schema_version") != 1
        or artifact.get("contract_id") != POLICY_CONTRACT_ID
        or artifact.get("run_id") != run_id
        or artifact.get("source_reflection_artifact_sha256")
        != hashlib.sha256(reflection_bytes).hexdigest()
        or artifact.get("applied") is not False
        or not isinstance(artifact.get("review_required"), bool)
        or not isinstance(candidates, list)
    ):
        raise ValueError("policy candidate artifact is invalid")
    validated = [_validated_policy_item(item) for item in candidates]
    policy_ids = [item["policy_id"] for item in validated]
    if len(policy_ids) != len(set(policy_ids)) or artifact["review_required"] != any(
        item["status"] == "review_required" for item in validated
    ):
        raise ValueError("policy candidate artifact is invalid")
    return artifact


def _matching_policy(artifact, policy_id):
    matches = [
        item
        for item in artifact["candidates"]
        if item.get("policy_id") == policy_id
    ]
    if len(matches) > 1:
        raise ValueError("policy candidate artifact has duplicate policy IDs")
    return matches[0] if matches else None


def _collect_evidence(
    state_dir, policy_id, workspace="personal", project=None
):
    from memory_agent import _load_loop_run

    state = Path(state_dir).expanduser()
    runs_dir = state / "loop_engineering" / "runs"
    if not runs_dir.exists():
        return None, [], []
    if runs_dir.is_symlink() or not runs_dir.is_dir():
        raise ValueError("loop runs directory is invalid")
    accepted = []
    blockers = []
    seen_fingerprints = set()
    policy_text = None
    for run_dir in sorted(runs_dir.iterdir(), key=lambda item: item.name):
        run_id = run_dir.name
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            continue
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise ValueError("loop run path is unsafe")
        paths, run_data, _ = _load_loop_run(state, run_id)
        run_workspace = run_data.get("workspace", "personal")
        run_project = run_data.get("project")
        if run_workspace != workspace or run_project != project:
            continue
        artifact = _validated_policy_artifact(
            paths["run"].parent, run_id, run_data
        )
        if artifact is None:
            continue
        item = _matching_policy(artifact, policy_id)
        if item is None:
            continue
        text = item["normalized_text"]
        if policy_text is None:
            policy_text = text
        elif policy_text != text:
            raise ValueError("policy identity collision detected")
        if item["status"] != "review_required":
            blockers.append(
                {
                    "run_id": run_id,
                    "status": item["status"],
                    "conflicts_with": sorted(item["conflicts_with"]),
                }
            )
            continue
        task_sha = run_data.get("task_sha256")
        result_sha = run_data.get("result_sha256")
        if (
            not isinstance(task_sha, str)
            or SHA256_PATTERN.fullmatch(task_sha) is None
            or not isinstance(result_sha, str)
            or SHA256_PATTERN.fullmatch(result_sha) is None
        ):
            raise ValueError("loop evidence fingerprint is invalid")
        fingerprint = hashlib.sha256(f"{task_sha}:{result_sha}".encode("ascii")).hexdigest()
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        accepted.append(
            {
                "run_id": run_id,
                "task_sha256": task_sha,
                "result_sha256": result_sha,
                "evidence_fingerprint": fingerprint,
                "outcome": run_data["outcome"],
            }
        )
    return policy_text, accepted, blockers


def _generation_dir(state_dir):
    state = Path(state_dir).expanduser()
    loop_dir = state / "loop_engineering"
    target = loop_dir / CANDIDATE_GENERATION_DIR
    for path in (state, loop_dir, target):
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise ValueError("candidate generation state path is unsafe")
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.resolve(strict=True).relative_to(state.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("candidate generation state path is unsafe") from exc
    return target


def _candidate_values(intent):
    text = intent["policy_text"]
    workspace = intent["workspace"]
    project = intent["project"]
    run_ids = intent["evidence_run_ids"]
    evidence_lines = "\n".join(f"- Loop run: {run_id}" for run_id in run_ids)
    content = (
        f"Proposed reusable principle:\n{text}\n\n"
        f"Independent Loop evidence: {len(run_ids)} runs.\n"
        f"{evidence_lines}\n\n"
        "This record is a candidate only and requires Review Gate approval."
    )
    values = {
        "type": "principle",
        "title": f"Loop-learned principle: {text[:80]}",
        "scope": "project" if project else "global",
        "workspace": workspace,
        "confidentiality": "personal" if workspace == "personal" else "internal",
        "source": "loop-engineering:stage13",
        "content": content,
        "action": "create",
        "confidence": "inferred",
        "evidence": [f"loop-run:{run_id}" for run_id in run_ids],
        "source_refs": [
            f"loop-policy:{intent['policy_id']}",
            f"loop-candidate-request:{intent['request_id']}",
        ],
        "relations": [],
        "tags": ["loop-learning", "low-risk-candidate", f"policy:{intent['policy_id']}"],
    }
    if project:
        values["project"] = project
    return values


def _new_intent(request, request_id, policy_text, evidence):
    return {
        "schema_version": 1,
        "contract_id": CANDIDATE_GENERATION_CONTRACT_ID,
        "request_id": request_id,
        "policy_id": request["policy_id"],
        "policy_text": policy_text,
        "workspace": request["workspace"],
        "project": request["project"],
        "minimum_independent_evidence": MINIMUM_INDEPENDENT_EVIDENCE,
        "evidence_run_ids": [item["run_id"] for item in evidence],
        "evidence_fingerprints": [item["evidence_fingerprint"] for item in evidence],
        "candidate_id": None,
        "candidate_path": None,
        "status": "pending_creation",
        "applied": False,
    }


def _validate_intent(intent, request, request_id):
    if (
        not isinstance(intent, dict)
        or intent.get("schema_version") != 1
        or intent.get("contract_id") != CANDIDATE_GENERATION_CONTRACT_ID
        or intent.get("request_id") != request_id
        or intent.get("policy_id") != request["policy_id"]
        or intent.get("workspace") != request["workspace"]
        or intent.get("project") != request["project"]
        or intent.get("minimum_independent_evidence") != MINIMUM_INDEPENDENT_EVIDENCE
        or not isinstance(intent.get("policy_text"), str)
        or _policy_id(intent["policy_text"]) != request["policy_id"]
        or not isinstance(intent.get("evidence_run_ids"), list)
        or len(intent["evidence_run_ids"]) < MINIMUM_INDEPENDENT_EVIDENCE
        or len(set(intent["evidence_run_ids"])) != len(intent["evidence_run_ids"])
        or not all(RUN_ID_PATTERN.fullmatch(item) for item in intent["evidence_run_ids"])
        or not isinstance(intent.get("evidence_fingerprints"), list)
        or len(intent["evidence_fingerprints"]) != len(intent["evidence_run_ids"])
        or len(set(intent["evidence_fingerprints"])) != len(intent["evidence_fingerprints"])
        or not all(SHA256_PATTERN.fullmatch(item) for item in intent["evidence_fingerprints"])
        or intent.get("status") not in {"pending_creation", "candidate"}
        or intent.get("applied") is not False
    ):
        raise ValueError("candidate generation artifact is invalid")
    if intent["status"] == "pending_creation":
        if intent.get("candidate_id") is not None or intent.get("candidate_path") is not None:
            raise ValueError("candidate generation artifact is invalid")
    else:
        if (
            not isinstance(intent.get("candidate_id"), str)
            or not intent["candidate_id"]
            or not isinstance(intent.get("candidate_path"), str)
            or not intent["candidate_path"]
        ):
            raise ValueError("candidate generation artifact is invalid")
    return intent


def _revalidate_intent_evidence(state_dir, request, intent):
    policy_text, evidence, blockers = _collect_evidence(
        state_dir,
        request["policy_id"],
        request["workspace"],
        request["project"],
    )
    if blockers or policy_text != intent["policy_text"]:
        raise ValueError("candidate generation evidence is no longer reviewable")
    current = {
        (item["run_id"], item["evidence_fingerprint"])
        for item in evidence
    }
    recorded = set(
        zip(intent["evidence_run_ids"], intent["evidence_fingerprints"])
    )
    if (
        len(evidence) < MINIMUM_INDEPENDENT_EVIDENCE
        or not recorded.issubset(current)
    ):
        raise ValueError("candidate generation evidence is invalid")


def _result_output(request_id, request, intent, record, reused):
    return {
        "request_id": request_id,
        "policy_id": request["policy_id"],
        "eligible": True,
        "reason": "threshold_met",
        "independent_evidence_count": len(intent["evidence_run_ids"]),
        "minimum_independent_evidence": MINIMUM_INDEPENDENT_EVIDENCE,
        "candidate_id": intent["candidate_id"],
        "candidate_path": intent["candidate_path"],
        "memory_status": record["status"],
        "reused": reused,
        "applied": False,
    }


class LowRiskCandidateAgent(BaseAgent):
    agent_id = "low_risk_candidate_agent"
    handles = ["loop.generate-candidate"]

    def __init__(self, core, state_dir):
        if not callable(getattr(core, "create_candidate", None)) or not callable(
            getattr(core, "get", None)
        ):
            raise ValueError("memory core is invalid")
        if isinstance(state_dir, bool):
            raise ValueError("state_dir must be a filesystem path")
        self.core = core
        self.state_dir = Path(state_dir).expanduser()

    def run(self, task, context):
        if not self.can_handle(task):
            raise ValueError("task type is not handled by this agent")
        values = task.get("input")
        if not isinstance(values, dict) or not set(values) <= {
            "policy_id",
            "workspace",
            "project",
        }:
            raise ValueError("task.input has unsupported keys")
        if set(values) not in (
            {"policy_id", "workspace"},
            {"policy_id", "workspace", "project"},
        ):
            raise ValueError("task.input must contain policy_id and workspace")
        request, request_id = _request(
            values.get("policy_id"), values.get("workspace"), values.get("project")
        )
        artifact_path = _generation_dir(self.state_dir) / f"{request_id}.json"
        reused = artifact_path.exists()
        if reused:
            intent = _validate_intent(
                _regular_json(artifact_path, "candidate generation artifact is invalid"),
                request,
                request_id,
            )
            _revalidate_intent_evidence(self.state_dir, request, intent)
        else:
            policy_text, evidence, blockers = _collect_evidence(
                self.state_dir,
                request["policy_id"],
                request["workspace"],
                request["project"],
            )
            if blockers:
                output = {
                    "request_id": request_id,
                    "policy_id": request["policy_id"],
                    "eligible": False,
                    "reason": "policy_not_reviewable",
                    "independent_evidence_count": len(evidence),
                    "minimum_independent_evidence": MINIMUM_INDEPENDENT_EVIDENCE,
                    "candidate_id": None,
                    "candidate_path": None,
                    "memory_status": None,
                    "applied": False,
                }
                return self.result(task, output, candidates=[], confidence=1.0)
            if policy_text is None or len(evidence) < MINIMUM_INDEPENDENT_EVIDENCE:
                output = {
                    "request_id": request_id,
                    "policy_id": request["policy_id"],
                    "eligible": False,
                    "reason": "insufficient_independent_evidence",
                    "independent_evidence_count": len(evidence),
                    "minimum_independent_evidence": MINIMUM_INDEPENDENT_EVIDENCE,
                    "candidate_id": None,
                    "candidate_path": None,
                    "memory_status": None,
                    "applied": False,
                }
                return self.result(task, output, candidates=[], confidence=1.0)
            intent = _new_intent(request, request_id, policy_text, evidence)
            atomic_write_text(
                artifact_path,
                json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )

        if intent["status"] == "candidate":
            record = self.core.get(intent["candidate_id"])
            if (
                record.get("status") not in MEMORY_REVIEW_STATES
                or record.get("relative_path") != intent["candidate_path"]
                or f"loop-candidate-request:{request_id}"
                not in record.get("source_refs", [])
            ):
                raise ValueError("generated candidate no longer matches its artifact")
            candidates = [intent["candidate_id"]] if record["status"] == "candidate" else []
            return self.result(
                task,
                _result_output(request_id, request, intent, record, True),
                candidates=candidates,
                confidence=1.0,
            )

        created = self.core.create_candidate(_candidate_values(intent))
        completed = dict(intent)
        completed["candidate_id"] = created["candidate_id"]
        completed["candidate_path"] = created["path"]
        completed["status"] = "candidate"
        atomic_write_text(
            artifact_path,
            json.dumps(completed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        record = self.core.get(created["candidate_id"])
        return self.result(
            task,
            _result_output(request_id, request, completed, record, reused),
            candidates=[created["candidate_id"]],
            confidence=1.0,
        )


__all__ = ["LowRiskCandidateAgent"]
