import argparse
import hashlib
import json
import sys
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
        memory.check_state_dir(root, state)
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
        content=f"Original task:\n{task}\n\nCompleted task result:\n{result}",
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


def finalize_memory(root, task, result, state_dir=None):
    if not isinstance(task, str) or not task.strip():
        raise AgentError("missing_input", "Original task text is required.")
    if result is None or not isinstance(result, str):
        raise AgentError("missing_input", "Completed task result is required.")
    root, state = _validated_paths(root, state_dir)
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
            raise AgentError(
                "distillation_failed", "Candidate distillation failed."
            ) from exc
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "operation": "finalize",
        "candidate_count": len(artifacts),
        "review_required": True,
        "applied": False,
        "artifacts": artifacts,
        "warnings": [],
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
    return parser


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
    operation = argv[0] if argv and argv[0] in {"prepare", "finalize"} else "unknown"
    try:
        if not argv or (argv[0] not in {"prepare", "finalize", "-h", "--help"}):
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
        else:
            payload = finalize_memory(
                args.root,
                args.task,
                _result_input(args),
                state_dir=args.state_dir,
            )
    except AgentError as exc:
        _emit(_error_payload(operation, exc))
        if exc.code in {
            "invalid_arguments",
            "invalid_max_chars",
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
