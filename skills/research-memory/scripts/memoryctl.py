#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_REPO = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
DEFAULT_STATE = Path.home() / "Library/Application Support/ResearchAgent"


class ConfigError(Exception):
    pass


def _path_from_env(name, default):
    return Path(os.environ.get(name, str(default))).expanduser()


def _require_dir(path, env_name):
    if not path.exists() or not path.is_dir():
        raise ConfigError(f"{env_name} does not exist or is not a directory: {path}")
    return path


def _require_file(path, label):
    if not path.exists() or not path.is_file():
        raise ConfigError(f"{label} does not exist or is not a file: {path}")
    return path


def load_config():
    repo = _require_dir(_path_from_env("RESEARCH_MEMORY_REPO", DEFAULT_REPO), "RESEARCH_MEMORY_REPO")
    root = _require_dir(_path_from_env("RESEARCH_MEMORY_ROOT", DEFAULT_ROOT), "RESEARCH_MEMORY_ROOT")
    state = _require_dir(_path_from_env("RESEARCH_MEMORY_STATE", DEFAULT_STATE), "RESEARCH_MEMORY_STATE")
    memory_py = _require_file(repo / "src" / "memory.py", "src/memory.py")
    tools_py = _require_file(repo / "src" / "memory_tools.py", "src/memory_tools.py")
    return {
        "repo": repo,
        "root": root,
        "state": state,
        "memory_py": memory_py,
        "tools_py": tools_py,
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Thin wrapper for local Research Agent Memory.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show configured paths.")

    search = sub.add_parser("search", help="Search the local memory index.")
    search.add_argument("query")

    for name in ("context", "recall"):
        context = sub.add_parser(name, help="Generate a bounded Agent Context Pack.")
        context.add_argument("query")
        context.add_argument("--project")
        context.add_argument("--workspace", choices=("personal", "work"))
        context.add_argument("--max-chars", type=int)

    sub.add_parser("validate", help="Validate memory files.")
    sub.add_parser("index", help="Index memory files into local SQLite.")
    sub.add_parser("doctor", help="Inspect memory and index health.")

    project_status = sub.add_parser("project-status", help="Summarize one project's memory health.")
    project_status.add_argument("project")

    import_file = sub.add_parser("import-file", help="Dry-run and then import one local file.")
    import_file.add_argument("path")
    import_file.add_argument("--dry-run-only", action="store_true")

    add = sub.add_parser("add", help="Forward arguments to memory.py add.")
    add.add_argument("args", nargs=argparse.REMAINDER)

    return parser


def _run(command):
    return subprocess.run(command).returncode


def _memory_command(config, command, *args):
    return [sys.executable, str(config["memory_py"]), command, *args]


def _tools_command(config, command, *args):
    return [sys.executable, str(config["tools_py"]), command, *args]


def command_for_args(args, config):
    root = str(config["root"])
    state = str(config["state"])

    if args.command == "search":
        return _memory_command(config, "search", args.query, "--root", root, "--state-dir", state)
    if args.command in {"context", "recall"}:
        command = _memory_command(
            config,
            "context",
            args.query,
            "--root",
            root,
            "--state-dir",
            state,
            "--format",
            "json",
        )
        for option in ("project", "workspace", "max_chars"):
            value = getattr(args, option)
            if value is not None:
                command.extend((f"--{option.replace('_', '-')}", str(value)))
        return command
    if args.command == "validate":
        return _memory_command(config, "validate", "--root", root)
    if args.command == "index":
        return _memory_command(config, "index", "--root", root, "--state-dir", state)
    if args.command == "doctor":
        return _memory_command(config, "doctor", "--root", root, "--state-dir", state, "--json")
    if args.command == "project-status":
        return _memory_command(
            config,
            "project-status",
            "--root",
            root,
            "--state-dir",
            state,
            "--project",
            args.project,
            "--json",
        )
    if args.command == "add":
        return _memory_command(config, "add", "--root", root, *args.args)
    raise ConfigError(f"unsupported command: {args.command}")


def import_file(args, config):
    source = _require_file(Path(args.path).expanduser(), "import-file path")
    root = str(config["root"])
    base = _tools_command(
        config,
        "import-manual",
        "--path",
        str(source),
        "--root",
        root,
    )
    dry_run = [*base, "--dry-run"]
    dry_code = _run(dry_run)
    if dry_code != 0 or args.dry_run_only:
        return dry_code
    return _run(base)


def print_status(config):
    print(f"Repository: {config['repo']}")
    print(f"Memory root: {config['root']}")
    print(f"State: {config['state']}")
    print(f"memory.py: {config['memory_py']}")
    print(f"memory_tools.py: {config['tools_py']}")


def parse_args(parser, argv):
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "add":
        args = parser.parse_args(["add"])
        args.args = values[1:]
        return args
    return parser.parse_args(values)


def main(argv=None):
    parser = build_parser()
    args = parse_args(parser, argv)
    try:
        config = load_config()
        if args.command == "status":
            print_status(config)
            return 0
        if args.command == "import-file":
            return import_file(args, config)
        return _run(command_for_args(args, config))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
