import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


if __package__:
    from . import memory, platform_paths

    sys.modules.setdefault("memory", memory)
    from . import memory_distill, memory_tools
else:
    import memory
    import memory_distill
    import memory_tools
    import platform_paths


SCHEMA_VERSION = 1
DEFAULT_MAX_CHARS = 8000
DEFAULT_MAX_TASK_CHARS = 20_000
DEFAULT_MAX_RESULT_CHARS = 60_000
DEFAULT_MAX_CANDIDATE_CHARS = 80_000
LOOP_REVIEW_PLACEHOLDER = "Pending human or agent review."
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
POLICY_LINE_PATTERN = re.compile(
    r"- \[([ xX])\] (.+) <!-- policy_id: ([0-9a-f]{16}) -->"
)


class AgentError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise AgentError("invalid_arguments", "Invalid command arguments.")


def _validated_paths(root, state_dir):
    try:
        root = memory._require_data_root(root)
    except (OSError, ValueError) as exc:
        raise AgentError("invalid_memory_root", "Memory root is not initialized.") from exc

    state = Path(state_dir).expanduser() if state_dir else platform_paths.default_state_dir()
    try:
        state, _ = memory.check_state_dir(root, state)
    except (OSError, ValueError) as exc:
        raise AgentError("invalid_state_dir", "State directory is not safe.") from exc
    return root, state


def _source(row):
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "kind": row.get("kind"),
        "source_path": row.get("relative_path"),
    }


def _context_entry(row):
    title = str(row.get("title") or row.get("id") or "Untitled")
    source_path = str(row.get("relative_path") or "")
    excerpt = str(row.get("excerpt") or "")
    lines = [f"## {title}"]
    if source_path:
        lines.append(f"Source: {source_path}")
    if excerpt:
        lines.append(excerpt)
    return "\n".join(lines)


def build_context(rows, max_chars):
    context = ""
    sources = []
    seen = set()
    for row in rows:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        entry = _context_entry(row)
        separator = "\n\n" if context else ""
        remaining = max_chars - len(context)
        if remaining <= len(separator):
            break
        piece = entry[: remaining - len(separator)]
        if not piece:
            break
        context += separator + piece
        sources.append(_source(row))
        if len(piece) < len(entry):
            break
    return context, sources


def prepare_memory(root, task, state_dir=None, project=None, max_chars=DEFAULT_MAX_CHARS):
    if not isinstance(max_chars, int) or max_chars <= 0:
        raise AgentError("invalid_max_chars", "max_chars must be greater than zero.")
    if not isinstance(task, str) or not task.strip():
        raise AgentError("missing_input", "Task text is required.")
    root, state = _validated_paths(root, state_dir)
    args = argparse.Namespace(
        root=str(root),
        state_dir=str(state),
        query=task,
        kind="all",
        source_kind=None,
        type=None,
        project=project,
        context_id=None,
        workspace=None,
        status=None,
        as_of=None,
        include_unassigned=True,
        include_restricted=False,
        include_inactive=False,
        limit=20,
    )
    try:
        rows = memory_tools.search_store(args)
    except Exception as exc:
        raise AgentError("search_failed", "Memory search failed.") from exc
    context, sources = build_context(rows, max_chars)
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "operation": "prepare",
        "context": context,
        "context_chars": len(context),
        "max_chars": max_chars,
        "sources": sources,
        "warnings": [],
    }


def _candidate_content(task, result):
    return f"Original task:\n{task}\n\nCompleted task result:\n{result}"


def _candidate_args(root, state, task, result):
    task_text = " ".join(task.split())
    task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
    result_hash = hashlib.sha256(result.encode("utf-8")).hexdigest()
    return argparse.Namespace(
        root=str(root),
        state_dir=str(state),
        action="ADD",
        confirmed=False,
        dry_run=False,
        max_iterations=2,
        type="session",
        title=f"Task result: {task_text[:80]}",
        scope="global",
        workspace="personal",
        confidentiality="personal",
        source="memory-agent",
        confidence="inferred",
        content=_candidate_content(task, result),
        target_id=None,
        project=None,
        context_id=None,
        source_id=None,
        source_path=None,
        source_sha256=None,
        evidence=[],
        source_refs=[
            f"memory-agent:task:{task_hash}",
            f"memory-agent:result:{result_hash}",
        ],
        relations=[],
        tags=["memory-agent"],
    )


def _validate_finalize_input(
    task, result, max_task_chars, max_result_chars, max_candidate_chars
):
    for value in (max_task_chars, max_result_chars, max_candidate_chars):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AgentError("invalid_max_chars", "Maximum character limits must be positive integers.")
    if not isinstance(task, str) or not task.strip():
        raise AgentError("missing_input", "Original task text is required.")
    if result is None or not isinstance(result, str):
        raise AgentError("missing_input", "Completed task result is required.")
    if len(task) > max_task_chars:
        raise AgentError("task_too_large", "Original task exceeds the allowed size.")
    if len(result) > max_result_chars:
        raise AgentError("result_too_large", "Completed task result exceeds the allowed size.")
    if result.strip() and len(_candidate_content(task, result)) > max_candidate_chars:
        raise AgentError("candidate_too_large", "Candidate memory exceeds the allowed size.")


def _validate_loop_input(outcome, error, reflection, policy_suggestions):
    if outcome not in {None, "pass", "fail"}:
        raise AgentError("invalid_arguments", "Invalid command arguments.")
    if error is not None and not isinstance(error, str):
        raise AgentError("invalid_arguments", "Invalid command arguments.")
    if reflection is not None and not isinstance(reflection, str):
        raise AgentError("invalid_arguments", "Invalid command arguments.")
    if policy_suggestions is not None and (
        not isinstance(policy_suggestions, (list, tuple))
        or any(not isinstance(item, str) for item in policy_suggestions)
    ):
        raise AgentError("invalid_arguments", "Invalid command arguments.")


def _reflection_text(task, result, outcome, error, reflection):
    supplied = reflection if reflection and reflection.strip() else LOOP_REVIEW_PLACEHOLDER
    worked = supplied if outcome == "pass" else LOOP_REVIEW_PLACEHOLDER
    failed = supplied if outcome == "fail" else LOOP_REVIEW_PLACEHOLDER
    return (
        "# Reflection\n\n"
        f"## Task\n\n{task}\n\n"
        f"## Outcome\n\n{outcome}\n\n"
        f"## Result Evidence\n\n{result or 'None'}\n\n"
        f"## Error Evidence\n\n{error if error and error.strip() else 'None'}\n\n"
        f"## What Worked\n\n{worked}\n\n"
        f"## What Failed\n\n{failed}\n\n"
        f"## Root Cause\n\n{LOOP_REVIEW_PLACEHOLDER}\n\n"
        f"## Next Change\n\n{LOOP_REVIEW_PLACEHOLDER}\n"
    )


def _normalized_policy(value):
    return " ".join(value.split())


def _policy_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _policy_suggestions_text(policy_suggestions):
    policies = [
        normalized
        for normalized in (_normalized_policy(item) for item in policy_suggestions or [])
        if normalized
    ]
    if not policies:
        return (
            "# Policy Suggestions\n\n"
            "No policy suggestion was supplied. Human review is required.\n"
        )
    lines = ["# Policy Suggestions", ""]
    lines.extend(
        f"- [ ] {policy} <!-- policy_id: {_policy_id(policy)} -->"
        for policy in policies
    )
    return "\n".join(lines) + "\n"


def _loop_runs_dir(state, create):
    loop_dir = state / "loop_engineering"
    runs = loop_dir / "runs"
    try:
        for path in (state, loop_dir, runs):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise AgentError("invalid_state_dir", "State directory is not safe.")
        if create:
            runs.mkdir(parents=True, exist_ok=True)
        if runs.exists() and not runs.resolve(strict=True).is_relative_to(
            state.resolve(strict=True)
        ):
            raise AgentError("invalid_state_dir", "State directory is not safe.")
    except AgentError:
        raise
    except OSError as exc:
        code = "loop_write_failed" if create else "invalid_state_dir"
        message = (
            "Loop Engineering run could not be created."
            if create
            else "State directory is not safe."
        )
        raise AgentError(code, message) from exc
    return runs


def _create_loop_run(state, task, result, outcome, error, reflection, policy_suggestions):
    runs = _loop_runs_dir(state, create=True)
    staging = None
    try:
        for _ in range(10):
            run_id = uuid.uuid4().hex
            run_dir = runs / run_id
            if not run_dir.exists():
                break
        else:
            raise OSError("unique run identifier could not be created")
        staging = Path(tempfile.mkdtemp(prefix=f".tmp-{run_id}-", dir=runs))
        run_data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "result_sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
            "outcome": outcome,
            "candidate_ids": [],
            "reflection_path": "reflection.md",
            "policy_suggestions_path": "policy_suggestions.md",
            "review_required": True,
            "approved_policy_count": 0,
            "status": "waiting_for_human",
        }
        memory.atomic_write_text(
            staging / "run.json",
            json.dumps(run_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        memory.atomic_write_text(
            staging / "reflection.md",
            _reflection_text(task, result, outcome, error, reflection),
        )
        memory.atomic_write_text(
            staging / "policy_suggestions.md",
            _policy_suggestions_text(policy_suggestions),
        )
        os.replace(staging, run_dir)
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise AgentError(
            "loop_write_failed", "Loop Engineering run could not be created."
        ) from exc
    return {
        "run_id": run_id,
        "path": run_dir.relative_to(state).as_posix(),
    }, run_dir


def finalize_memory(
    root,
    task,
    result,
    state_dir=None,
    max_task_chars=DEFAULT_MAX_TASK_CHARS,
    max_result_chars=DEFAULT_MAX_RESULT_CHARS,
    max_candidate_chars=DEFAULT_MAX_CANDIDATE_CHARS,
    outcome=None,
    error=None,
    reflection=None,
    policy_suggestions=None,
):
    _validate_finalize_input(
        task, result, max_task_chars, max_result_chars, max_candidate_chars
    )
    _validate_loop_input(outcome, error, reflection, policy_suggestions)
    root, state = _validated_paths(root, state_dir)
    loop_run = None
    run_dir = None
    if outcome is not None:
        loop_run, run_dir = _create_loop_run(
            state, task, result, outcome, error, reflection, policy_suggestions
        )
    artifacts = []
    if result.strip():
        try:
            summary = memory_distill.apply_candidate(
                _candidate_args(root, state, task, result)
            )
            relative_path = summary.get("path")
            if relative_path:
                candidate_path = root / relative_path
                if not candidate_path.is_file():
                    raise ValueError("candidate artifact is missing")
                artifacts.append(
                    {
                        "kind": "candidate",
                        "id": summary["candidate_id"],
                        "path": relative_path,
                    }
                )
        except Exception as exc:
            if run_dir is not None:
                try:
                    shutil.rmtree(run_dir)
                except OSError as rollback_exc:
                    raise AgentError(
                        "loop_rollback_failed", "Loop Engineering rollback failed."
                    ) from rollback_exc
            raise AgentError(
                "distillation_failed", "Candidate distillation failed."
            ) from exc
    payload = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "operation": "finalize",
        "candidate_count": len(artifacts),
        "review_required": True,
        "applied": False,
        "artifacts": artifacts,
        "warnings": [],
    }
    if loop_run is not None:
        payload["loop_run"] = loop_run
    return payload


def _load_loop_run(state, run_id):
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise AgentError("invalid_run_id", "Run identifier is invalid.")
    runs = _loop_runs_dir(state, create=False)
    run_dir = runs / run_id
    try:
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise AgentError("loop_run_not_found", "Loop Engineering run was not found.")
        if run_dir.resolve(strict=True).parent != runs.resolve(strict=True):
            raise AgentError("invalid_run_id", "Run identifier is invalid.")
        paths = {
            "run": run_dir / "run.json",
            "reflection": run_dir / "reflection.md",
            "policies": run_dir / "policy_suggestions.md",
        }
        if any(path.is_symlink() or not path.is_file() for path in paths.values()):
            raise AgentError("invalid_loop_run", "Loop Engineering run is incomplete.")
        run_data = json.loads(paths["run"].read_text(encoding="utf-8"))
        policy_text = paths["policies"].read_text(encoding="utf-8")
    except AgentError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentError("invalid_loop_run", "Loop Engineering run is invalid.") from exc
    if (
        not isinstance(run_data, dict)
        or run_data.get("schema_version") != SCHEMA_VERSION
        or run_data.get("run_id") != run_id
        or run_data.get("outcome") not in {"pass", "fail"}
        or run_data.get("reflection_path") != "reflection.md"
        or run_data.get("policy_suggestions_path") != "policy_suggestions.md"
        or run_data.get("status") not in {"waiting_for_human", "completed"}
        or run_data.get("review_required") is not True
    ):
        raise AgentError("invalid_loop_run", "Loop Engineering run is invalid.")
    return paths, run_data, policy_text


def _checked_policies(policy_text):
    checked = []
    seen = set()
    for line in policy_text.splitlines():
        if not line.startswith("- ["):
            continue
        match = POLICY_LINE_PATTERN.fullmatch(line)
        if match is None:
            if line.startswith(("- [x]", "- [X]")):
                raise AgentError("invalid_loop_run", "Checked policy suggestion is invalid.")
            continue
        mark, policy, policy_id = match.groups()
        normalized = _normalized_policy(policy)
        if policy_id != _policy_id(normalized):
            raise AgentError("invalid_loop_run", "Checked policy suggestion is invalid.")
        if mark in {"x", "X"} and policy_id not in seen:
            seen.add(policy_id)
            checked.append((policy_id, normalized))
    return checked


def _rules_with_approved_policies(existing, policies, run_id, outcome, approved_at):
    known = set(re.findall(r"^## rule-([0-9a-f]{16})$", existing or "", re.MULTILINE))
    additions = []
    for policy_id, policy in policies:
        if policy_id in known:
            continue
        known.add(policy_id)
        additions.append(
            f"## rule-{policy_id}\n\n"
            f"- Policy: {policy}\n"
            f"- Source run: {run_id}\n"
            f"- Outcome: {outcome}\n"
            f"- Approved: {approved_at}\n"
        )
    if not additions:
        return existing, 0
    content = existing if existing is not None else "# Memory Rules\n"
    if not content.endswith("\n"):
        content += "\n"
    if not content.endswith("\n\n"):
        content += "\n"
    return content + "\n".join(additions), len(additions)


def approve_policies(root, state_dir=None, run_id=None, confirmed=False):
    if confirmed is not True:
        raise AgentError("confirmation_required", "Explicit confirmation is required.")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise AgentError("invalid_run_id", "Run identifier is invalid.")
    root, state = _validated_paths(root, state_dir)
    paths, run_data, policy_text = _load_loop_run(state, run_id)
    checked = _checked_policies(policy_text)
    rules_path = root / "memory_rules.md"
    try:
        if rules_path.is_symlink():
            raise OSError("memory rules path is not a regular file")
        existing = rules_path.read_text(encoding="utf-8") if rules_path.exists() else None
        approved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rules_text, added = _rules_with_approved_policies(
            existing, checked, run_id, run_data["outcome"], approved_at
        )
        completed = dict(run_data)
        completed["status"] = "completed"
        completed["approved_policy_count"] = len(checked)
        operations = []
        if added:
            operations.append((rules_path, rules_text))
        operations.append(
            (
                paths["run"],
                json.dumps(completed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        )
        memory._replace_transaction(operations, [])
    except Exception as exc:
        raise AgentError("approval_failed", "Policy approval failed.") from exc
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "operation": "approve-policies",
        "run_id": run_id,
        "status": "completed",
        "approved_policy_count": len(checked),
        "rules_added": added,
        "review_required": False,
        "applied": False,
    }


def build_parser():
    parser = JsonArgumentParser(description="Coordinate local memory recall and review candidates.")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    prepare = subparsers.add_parser("prepare", help="Prepare bounded task context.")
    prepare.add_argument("--root", required=True, help="Initialized memory root.")
    prepare.add_argument("--state-dir", help="Local SQLite state directory.")
    prepare.add_argument("--task", required=True, help="Task text used for recall.")
    prepare.add_argument("--project", help="Optional project identifier.")
    prepare.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)

    finalize = subparsers.add_parser("finalize", help="Create review candidates from a task result.")
    finalize.add_argument("--root", required=True, help="Initialized memory root.")
    finalize.add_argument("--state-dir", help="Local SQLite state directory.")
    finalize.add_argument("--task", required=True, help="Original task text.")
    finalize.add_argument("--result", help="Completed task result text.")
    finalize.add_argument("--result-file", help="UTF-8 file containing the task result.")
    finalize.add_argument("--max-task-chars", default=DEFAULT_MAX_TASK_CHARS)
    finalize.add_argument("--max-result-chars", default=DEFAULT_MAX_RESULT_CHARS)
    finalize.add_argument("--max-candidate-chars", default=DEFAULT_MAX_CANDIDATE_CHARS)
    finalize.add_argument("--outcome", choices=("pass", "fail"))
    finalize.add_argument("--error")
    finalize.add_argument("--reflection")
    finalize.add_argument("--policy-suggestion", action="append", dest="policy_suggestions")

    approve = subparsers.add_parser(
        "approve-policies", help="Register explicitly checked policy suggestions."
    )
    approve.add_argument("--root", required=True, help="Initialized memory root.")
    approve.add_argument("--state-dir", help="Local state directory.")
    approve.add_argument("--run-id", required=True, help="Loop Engineering run identifier.")
    approve.add_argument("--confirmed", action="store_true")
    return parser


def _positive_chars(value):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentError(
            "invalid_max_chars", "Maximum character limits must be positive integers."
        ) from exc
    if value <= 0:
        raise AgentError(
            "invalid_max_chars", "Maximum character limits must be positive integers."
        )
    return value


def _result_input(args):
    if args.result is not None and args.result_file is not None:
        raise AgentError(
            "conflicting_input", "Use either --result or --result-file, not both."
        )
    if args.result is None and args.result_file is None:
        raise AgentError("missing_input", "A completed task result is required.")
    if args.result_file is None:
        return args.result
    try:
        return Path(args.result_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentError("missing_input", "Result file could not be read.") from exc


def _error_payload(operation, error):
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "error": {"code": error.code, "message": error.message},
    }


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    operations = {"prepare", "finalize", "approve-policies"}
    operation = argv[0] if argv and argv[0] in operations else "unknown"
    try:
        if not argv or (argv[0] not in operations | {"-h", "--help"}):
            raise AgentError("invalid_subcommand", "A valid subcommand is required.")
        args = build_parser().parse_args(argv)
        operation = args.operation
        if operation == "prepare":
            payload = prepare_memory(
                args.root,
                args.task,
                state_dir=args.state_dir,
                project=args.project,
                max_chars=args.max_chars,
            )
        elif operation == "finalize":
            payload = finalize_memory(
                args.root,
                args.task,
                _result_input(args),
                state_dir=args.state_dir,
                max_task_chars=_positive_chars(args.max_task_chars),
                max_result_chars=_positive_chars(args.max_result_chars),
                max_candidate_chars=_positive_chars(args.max_candidate_chars),
                outcome=args.outcome,
                error=args.error,
                reflection=args.reflection,
                policy_suggestions=args.policy_suggestions,
            )
        else:
            payload = approve_policies(
                args.root,
                state_dir=args.state_dir,
                run_id=args.run_id,
                confirmed=args.confirmed,
            )
    except AgentError as exc:
        _emit(_error_payload(operation, exc))
        if exc.code in {
            "invalid_arguments",
            "invalid_max_chars",
            "task_too_large",
            "result_too_large",
            "candidate_too_large",
            "confirmation_required",
            "invalid_run_id",
            "loop_run_not_found",
            "invalid_loop_run",
            "missing_input",
            "conflicting_input",
            "invalid_subcommand",
        }:
            return 2
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
