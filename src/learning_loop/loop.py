from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..context.builder import ContextBuilder
from ..memory.candidate import CandidateStore
from ..memory.store import MemoryStore
from ..review.gate import ReviewGate


_STATES = {
    "created",
    "context_ready",
    "running",
    "completed",
    "failed",
    "interrupted",
    "reflected",
    "review_pending",
    "reviewed",
    "verified",
    "verification_failed",
}
_WORKSPACES = {"personal", "work"}
_CONFIDENTIALITY = {"public", "personal", "internal", "restricted"}


def _timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_json(path, value):
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact must be a JSON object")
    return value


def validate_task(task):
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    required = ("run_id", "task_id", "title", "instruction", "query", "workspace", "inputs", "criteria")
    for field in required:
        if field not in task:
            raise ValueError(f"task is missing {field}")
    for field in ("run_id", "task_id", "title", "instruction", "query"):
        _text(task[field], field)
    workspace = task["workspace"]
    if workspace not in _WORKSPACES:
        raise ValueError("workspace must be personal or work")
    confidentiality = task.get("confidentiality", "personal")
    if confidentiality not in _CONFIDENTIALITY:
        raise ValueError("invalid confidentiality")
    if confidentiality == "restricted":
        raise ValueError("restricted tasks cannot be sent to the learning-loop model")
    project = task.get("project")
    if project is not None:
        _text(project, "project")
    inputs = task["inputs"]
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("inputs must contain at least one relative path")
    for item in inputs:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("task input paths must be non-empty strings")
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("task input paths must stay inside work_root")
    criteria = task["criteria"]
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("criteria must contain at least one item")
    seen = set()
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("criterion must be an object")
        criterion_id = _text(item.get("id"), "criterion id")
        if criterion_id in seen:
            raise ValueError("criterion ids must be unique")
        seen.add(criterion_id)
        _text(item.get("description"), "criterion description")
        terms = item.get("required_terms")
        if not isinstance(terms, list) or not terms or not all(
            isinstance(term, str) and term.strip() for term in terms
        ):
            raise ValueError("criterion required_terms must be a non-empty string list")
        forbidden = item.get("forbidden_terms", [])
        if not isinstance(forbidden, list) or not all(
            isinstance(term, str) and term.strip() for term in forbidden
        ):
            raise ValueError("criterion forbidden_terms must be a string list")
        if item.get("strategy") is not None:
            _text(item["strategy"], "criterion strategy")
    minimum = task.get("minimum_score", 1.0)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not 0 <= minimum <= 1:
        raise ValueError("minimum_score must be between 0 and 1")
    limit = task.get("context_limit", 8000)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("context_limit must be a positive integer")
    baseline = task.get("baseline_run_id")
    if baseline is not None:
        _text(baseline, "baseline_run_id")
    return task


class LearningLoop:
    def __init__(self, data_root, state_dir, work_root, backend=None):
        self.data_root = Path(data_root).expanduser()
        self.state_dir = Path(state_dir).expanduser()
        self.work_root = Path(work_root).expanduser().resolve()
        self.backend = backend
        self.runs_root = self.state_dir / "learning_runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.memory_store = MemoryStore(self.data_root)
        self.candidates = CandidateStore(self.data_root, self.state_dir)
        self.context_builder = ContextBuilder(self.memory_store)
        self.review_gate = ReviewGate(self.data_root, self.state_dir)

    def _run_dir(self, run_id):
        run_id = _text(run_id, "run_id")
        if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be path-safe")
        return self.runs_root / run_id

    def _artifact(self, run_id, name):
        return self._run_dir(run_id) / name

    def _load_run(self, run_id):
        path = self._artifact(run_id, "run.json")
        if not path.is_file():
            raise ValueError("learning run not found")
        return _read_json(path)

    def _save_run(self, run, state=None, **updates):
        if state is not None:
            if state not in _STATES:
                raise ValueError("invalid learning run state")
            previous = run.get("state")
            if previous != state:
                history = list(run.get("state_history") or [])
                history.append({"state": state, "at": _timestamp()})
                run["state_history"] = history
                run["state"] = state
        run.update(updates)
        run["updated_at"] = _timestamp()
        _write_json(self._artifact(run["run_id"], "run.json"), run)
        return run

    def _create_or_load(self, task):
        run_id = task["run_id"].strip()
        task_digest = _digest_text(_canonical(task))
        run_path = self._artifact(run_id, "run.json")
        if run_path.is_file():
            run = _read_json(run_path)
            if run.get("task_digest") != task_digest:
                raise ValueError("run_id already belongs to a different task")
            return run
        run = {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task["task_id"],
            "task_digest": task_digest,
            "task": task,
            "state": "created",
            "state_history": [{"state": "created", "at": _timestamp()}],
            "created_at": _timestamp(),
            "updated_at": _timestamp(),
            "candidate_ids": [],
            "review_decisions": {},
        }
        self._run_dir(run_id).mkdir(parents=True, exist_ok=True)
        _write_json(run_path, run)
        return run

    def _resolve_inputs(self, task):
        evidence = []
        rendered = []
        for relative in task["inputs"]:
            path = (self.work_root / relative).resolve(strict=True)
            try:
                path.relative_to(self.work_root)
            except ValueError as exc:
                raise ValueError("task input escaped work_root") from exc
            if not path.is_file():
                raise ValueError("task input must be a file")
            content = path.read_text(encoding="utf-8")
            evidence.append(
                {
                    "path": relative,
                    "sha256": _digest_text(content),
                    "characters": len(content),
                }
            )
            rendered.append(f"## Input: {relative}\n\n{content}")
        return "\n\n".join(rendered), evidence

    def _build_context(self, task):
        context = self.context_builder.build(
            task["query"],
            limit=task.get("context_limit", 8000),
            workspace=task["workspace"],
            project=task.get("project"),
        )
        source_records = []
        for source_id in context["sources"]:
            record = self.memory_store.get(source_id)
            if record.get("status") != "active" or record.get("confidentiality") == "restricted":
                raise ValueError("context builder returned an ineligible memory")
            source_records.append(
                {
                    "id": source_id,
                    "type": record.get("type"),
                    "confidentiality": record.get("confidentiality"),
                    "source": record.get("source"),
                }
            )
        return {
            **context,
            "query": task["query"],
            "workspace": task["workspace"],
            "project": task.get("project"),
            "source_records": source_records,
            "restricted_source_count": 0,
        }

    @staticmethod
    def _prompt(task, context, inputs):
        criteria = "\n".join(
            f"- {item['id']}: {item['description']}"
            for item in task["criteria"]
        )
        system = (
            "You are executing a bounded LAOS learning-loop task. Use only the supplied "
            "inputs and active-memory context. Apply every supplied active-memory strategy "
            "explicitly, including naming each requested analysis method or artifact. Cite "
            "concrete evidence. When memory is used, include an exact `Memory used:` section "
            "listing its memory IDs. Do not claim that rejected or unavailable memory was used.\n\n"
            "ACTIVE MEMORY CONTEXT\n"
            + (context["text"] or "(none)")
        )
        user = (
            f"# Task\n{task['title']}\n\n{task['instruction']}\n\n"
            f"# Acceptance criteria\n{criteria}\n\n# Evidence inputs\n{inputs}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _call_backend(self, messages):
        if self.backend is None or not callable(getattr(self.backend, "complete", None)):
            raise ValueError("a model backend with complete() is required")
        response = self.backend.complete(messages)
        if isinstance(response, str):
            content = response
        elif isinstance(response, dict) and isinstance(response.get("content"), str):
            content = response["content"]
        else:
            raise ValueError("model backend returned an invalid task response")
        return _text(content, "model response")

    @staticmethod
    def _evaluate(task, result, context):
        haystack = result.casefold()
        criteria = []
        for item in task["criteria"]:
            missing = [term for term in item["required_terms"] if term.casefold() not in haystack]
            present_forbidden = [
                term
                for term in item.get("forbidden_terms", [])
                if term.casefold() in haystack
            ]
            passed = not missing and not present_forbidden
            criteria.append(
                {
                    "id": item["id"],
                    "description": item["description"],
                    "passed": passed,
                    "missing_terms": missing,
                    "forbidden_terms_present": present_forbidden,
                }
            )
        passed_count = sum(item["passed"] for item in criteria)
        score = passed_count / len(criteria)
        used_memory_ids = [source for source in context["sources"] if source in result]
        minimum = float(task.get("minimum_score", 1.0))
        return {
            "schema_version": 1,
            "status": "passed" if score >= minimum else "failed",
            "score": score,
            "minimum_score": minimum,
            "criteria": criteria,
            "passed_count": passed_count,
            "failed_count": len(criteria) - passed_count,
            "context_source_ids": list(context["sources"]),
            "used_memory_ids": used_memory_ids,
            "memory_attribution_complete": not context["sources"]
            or set(context["sources"]) <= set(used_memory_ids),
        }

    @staticmethod
    def _reflection(task, outcome):
        missed = [item for item in outcome["criteria"] if not item["passed"]]
        lines = [
            f"# Reflection — {task['run_id']}",
            "",
            f"Outcome: **{outcome['status']}**",
            f"Score: **{outcome['score']:.3f}**",
            "",
            "## What happened",
            "",
            "The task result did not satisfy every declared acceptance criterion."
            if missed
            else "The task satisfied the declared acceptance criteria.",
            "",
            "## Missed criteria",
            "",
        ]
        if not missed:
            lines.append("- None.")
        for item in missed:
            details = []
            if item["missing_terms"]:
                details.append("missing: " + ", ".join(item["missing_terms"]))
            if item["forbidden_terms_present"]:
                details.append("forbidden present: " + ", ".join(item["forbidden_terms_present"]))
            lines.append(f"- **{item['id']}** — {item['description']} ({'; '.join(details)})")
        lines.extend(
            [
                "",
                "## Reusable lesson",
                "",
                "Convert only missed, reusable criteria into reviewable strategy candidates. "
                "Do not update active memory without human approval.",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _suggestion_content(task, criterion):
        return criterion.get("strategy") or (
            f"When executing `{task.get('type', 'task')}` work, explicitly verify "
            f"`{criterion['description']}` and cite the evidence before declaring success."
        )

    def _create_candidates(self, task, outcome):
        candidates = []
        for item in outcome["criteria"]:
            if item["passed"]:
                continue
            criterion = next(value for value in task["criteria"] if value["id"] == item["id"])
            source_id = f"learning-loop:{task['run_id']}:criterion:{item['id']}"
            existing = self.candidates.find_by_source_id(source_id)
            if existing is None:
                values = {
                    "type": "principle",
                    "title": f"Learning strategy: {criterion['description']}",
                    "scope": "global",
                    "workspace": task["workspace"],
                    "confidentiality": task.get("candidate_confidentiality")
                    or ("personal" if task["workspace"] == "personal" else "internal"),
                    "source": "learning_loop",
                    "content": self._suggestion_content(task, criterion),
                    "action": "create",
                    "confidence": "inferred",
                    "source_id": source_id,
                    "evidence": [f"run:{task['run_id']}", f"criterion:{item['id']}"],
                    "source_refs": [
                        f"learning-run:{task['run_id']}:outcome.json",
                        f"learning-run:{task['run_id']}:reflection.md",
                    ],
                    "tags": ["learning-loop", task.get("type", "task"), item["id"]],
                }
                existing = self.candidates.create(values)
            candidates.append(
                {
                    "criterion_id": item["id"],
                    "source_id": source_id,
                    "candidate_id": existing["candidate_id"],
                    "status": existing["status"],
                    "content": self._suggestion_content(task, criterion),
                }
            )
        return candidates

    @staticmethod
    def _policy_markdown(task, candidates):
        lines = [f"# Policy suggestions — {task['run_id']}", ""]
        if not candidates:
            return "\n".join(lines + ["No strategy candidates were generated.", ""])
        for item in candidates:
            lines.extend(
                [
                    f"## {item['candidate_id']}",
                    "",
                    f"- Criterion: `{item['criterion_id']}`",
                    f"- Source ID: `{item['source_id']}`",
                    f"- Status: `{item['status']}`",
                    "",
                    item["content"],
                    "",
                ]
            )
        lines.append("These are candidates only. Active memory remains unchanged until Review Gate approval.")
        lines.append("")
        return "\n".join(lines)

    def execute(self, task):
        validate_task(task)
        run = self._create_or_load(task)
        run_id = task["run_id"]
        if run.get("state") in {
            "completed",
            "review_pending",
            "reviewed",
            "verified",
            "verification_failed",
        }:
            return self.status(run_id)

        try:
            context_path = self._artifact(run_id, "context.json")
            if context_path.is_file():
                context = _read_json(context_path)
            else:
                context = self._build_context(task)
                _write_json(context_path, context)
                run = self._save_run(run, "context_ready")

            result_path = self._artifact(run_id, "result.md")
            evidence_path = self._artifact(run_id, "evidence.json")
            inputs, input_evidence = self._resolve_inputs(task)
            if result_path.is_file():
                result = result_path.read_text(encoding="utf-8")
            else:
                run = self._save_run(run, "running", last_error=None)
                result = self._call_backend(self._prompt(task, context, inputs))
                _atomic_write(result_path, result.rstrip() + "\n")
            if not evidence_path.is_file():
                stored_result = result if result.endswith("\n") else result.rstrip() + "\n"
                _write_json(
                    evidence_path,
                    {
                        "schema_version": 1,
                        "run_id": run_id,
                        "task_digest": run["task_digest"],
                        "inputs": input_evidence,
                        "context_source_ids": list(context["sources"]),
                        "result_sha256": _digest_text(stored_result),
                    },
                )

            outcome_path = self._artifact(run_id, "outcome.json")
            if outcome_path.is_file():
                outcome = _read_json(outcome_path)
            else:
                outcome = self._evaluate(task, result, context)
                outcome["run_id"] = run_id
                _write_json(outcome_path, outcome)
                run = self._save_run(run, "completed" if outcome["status"] == "passed" else "failed")

            reflection_path = self._artifact(run_id, "reflection.md")
            if not reflection_path.is_file():
                _atomic_write(reflection_path, self._reflection(task, outcome))
            run = self._save_run(run, "reflected")

            candidates = self._create_candidates(task, outcome)
            _atomic_write(
                self._artifact(run_id, "policy_suggestions.md"),
                self._policy_markdown(task, candidates),
            )
            candidate_ids = [item["candidate_id"] for item in candidates]
            decisions = self._load_decisions(run_id)["decisions"]
            if run.get("state") in {"verified", "verification_failed"}:
                target_state = run["state"]
            elif candidate_ids and set(candidate_ids) <= set(decisions):
                target_state = "reviewed"
            else:
                target_state = "review_pending" if candidate_ids else "completed"
            run = self._save_run(
                run,
                target_state,
                candidate_ids=candidate_ids,
                candidate_source_ids=[item["source_id"] for item in candidates],
                outcome_status=outcome["status"],
                score=outcome["score"],
            )
            return self.status(run_id)
        except Exception as exc:
            self._save_run(run, "interrupted", last_error=str(exc))
            raise

    def _decisions_path(self, run_id):
        return self._artifact(run_id, "review_decisions.json")

    def _load_decisions(self, run_id):
        path = self._decisions_path(run_id)
        if not path.is_file():
            return {"schema_version": 1, "run_id": run_id, "decisions": {}}
        return _read_json(path)

    def _write_memory_rules(self, run_id, decisions):
        accepted = []
        rejected = []
        for candidate_id, item in sorted(decisions["decisions"].items()):
            if item["action"] == "accept":
                accepted.append(self.memory_store.get(candidate_id))
            else:
                rejected.append(candidate_id)
        lines = [f"# Memory rules — {run_id}", "", "## Accepted", ""]
        if not accepted:
            lines.append("- None.")
        for record in accepted:
            lines.extend([f"### {record['id']}", "", record.get("content", ""), ""])
        lines.extend(["## Rejected", ""])
        lines.extend([f"- `{candidate_id}`" for candidate_id in rejected] or ["- None."])
        lines.append("")
        _atomic_write(self._artifact(run_id, "memory_rules.md"), "\n".join(lines))

    def review(self, run_id, candidate_id, action, reason=None):
        if action not in {"accept", "reject"}:
            raise ValueError("review action must be accept or reject")
        run = self._load_run(run_id)
        if candidate_id not in set(run.get("candidate_ids") or []):
            raise ValueError("candidate does not belong to this learning run")
        decisions = self._load_decisions(run_id)
        existing = decisions["decisions"].get(candidate_id)
        if existing is not None:
            if existing["action"] != action:
                raise ValueError("candidate already has a different review decision")
            return self.status(run_id)

        record = self.memory_store.get(candidate_id)
        already_applied = (
            action == "accept" and record.get("status") == "active"
        ) or (
            action == "reject" and record.get("audit_status") == "rejected"
        )
        result = (
            {"status": "already_reviewed", "candidate_id": candidate_id, "action": action}
            if already_applied
            else self.review_gate.review(
                action,
                candidate_id,
                reason=reason,
                workspace=run["task"]["workspace"],
            )
        )
        decisions["decisions"][candidate_id] = {
            "action": action,
            "reason": reason,
            "reviewed_at": _timestamp(),
            "result": result,
        }
        _write_json(self._decisions_path(run_id), decisions)
        self._write_memory_rules(run_id, decisions)
        all_decided = set(run.get("candidate_ids") or []) <= set(decisions["decisions"])
        run = self._save_run(
            run,
            "reviewed" if all_decided else "review_pending",
            review_decisions={key: value["action"] for key, value in decisions["decisions"].items()},
        )
        return self.status(run_id)

    def compare(self, first_run_id, second_run_id, minimum_improvement=0.2):
        if (
            isinstance(minimum_improvement, bool)
            or not isinstance(minimum_improvement, (int, float))
            or minimum_improvement < 0
        ):
            raise ValueError("minimum_improvement must be a non-negative number")
        self._load_run(first_run_id)
        second = self._load_run(second_run_id)
        first_outcome = _read_json(self._artifact(first_run_id, "outcome.json"))
        second_outcome = _read_json(self._artifact(second_run_id, "outcome.json"))
        second_context = _read_json(self._artifact(second_run_id, "context.json"))
        second_result = self._artifact(second_run_id, "result.md").read_text(encoding="utf-8")
        decisions = self._load_decisions(first_run_id)["decisions"]
        accepted = sorted(
            candidate_id for candidate_id, item in decisions.items() if item["action"] == "accept"
        )
        rejected = sorted(
            candidate_id for candidate_id, item in decisions.items() if item["action"] == "reject"
        )
        context_sources = set(second_context.get("sources") or [])
        accepted_in_context = [item for item in accepted if item in context_sources]
        accepted_in_result = [item for item in accepted_in_context if item in second_result]
        rejected_in_context = [item for item in rejected if item in context_sources]
        restricted_sources = []
        for source_id in context_sources:
            record = self.memory_store.get(source_id)
            if record.get("confidentiality") == "restricted":
                restricted_sources.append(source_id)
        delta = second_outcome["score"] - first_outcome["score"]
        checks = {
            "minimum_improvement": delta >= minimum_improvement,
            "accepted_strategy_injected": bool(accepted_in_context),
            "accepted_strategy_attributed": bool(accepted_in_result),
            "rejected_strategy_excluded": not rejected_in_context,
            "restricted_memory_excluded": not restricted_sources,
            "second_run_not_worse": second_outcome["failed_count"] <= first_outcome["failed_count"],
        }
        passed = all(checks.values())
        comparison = {
            "schema_version": 1,
            "first_run_id": first_run_id,
            "second_run_id": second_run_id,
            "first_score": first_outcome["score"],
            "second_score": second_outcome["score"],
            "score_delta": delta,
            "minimum_improvement": minimum_improvement,
            "accepted_candidate_ids": accepted,
            "rejected_candidate_ids": rejected,
            "accepted_in_context": accepted_in_context,
            "accepted_in_result": accepted_in_result,
            "rejected_in_context": rejected_in_context,
            "restricted_sources": restricted_sources,
            "checks": checks,
            "passed": passed,
            "provenance": {
                "run": f"learning-run:{first_run_id}",
                "evidence": f"learning-run:{first_run_id}:evidence.json",
                "candidates": accepted + rejected,
                "review": f"learning-run:{first_run_id}:review_decisions.json",
                "active_memory": accepted,
            },
        }
        _write_json(self._artifact(second_run_id, "comparison.json"), comparison)
        lines = [
            f"# Learning-loop comparison — {first_run_id} → {second_run_id}",
            "",
            f"- First score: `{first_outcome['score']:.3f}`",
            f"- Second score: `{second_outcome['score']:.3f}`",
            f"- Delta: `{delta:.3f}`",
            f"- Verdict: `{'PASS' if passed else 'FAIL'}`",
            "",
            "## Checks",
            "",
        ]
        lines.extend(f"- [{'x' if value else ' '}] {name}" for name, value in checks.items())
        lines.extend(
            [
                "",
                "## Memory reuse",
                "",
                "- Accepted in context: " + (", ".join(accepted_in_context) if accepted_in_context else "none"),
                "- Accepted cited in result: " + (", ".join(accepted_in_result) if accepted_in_result else "none"),
                "- Rejected in context: " + (", ".join(rejected_in_context) if rejected_in_context else "none"),
                "",
            ]
        )
        _atomic_write(self._artifact(second_run_id, "comparison.md"), "\n".join(lines))
        self._save_run(second, "verified" if passed else "verification_failed")
        return comparison

    def status(self, run_id):
        run = self._load_run(run_id)
        artifacts = {}
        for name in (
            "run.json",
            "context.json",
            "result.md",
            "evidence.json",
            "outcome.json",
            "reflection.md",
            "policy_suggestions.md",
            "review_decisions.json",
            "memory_rules.md",
            "comparison.json",
            "comparison.md",
        ):
            path = self._artifact(run_id, name)
            if path.is_file():
                artifacts[name] = str(path)
        output = dict(run)
        output["artifacts"] = artifacts
        return output
