#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.learning_loop import LearningLoop
from src.models import build_model_backend


def _json_file(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON file must contain an object")
    return value


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--work-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run")
    run.add_argument("--task-file", required=True)
    run.add_argument("--model-config")

    review = commands.add_parser("review")
    review.add_argument("--run-id", required=True)
    review.add_argument("--candidate-id", required=True)
    review.add_argument("--action", choices=("accept", "reject"), required=True)
    review.add_argument("--reason")

    compare = commands.add_parser("compare")
    compare.add_argument("--first-run", required=True)
    compare.add_argument("--second-run", required=True)
    compare.add_argument("--minimum-improvement", type=float, default=0.2)

    status = commands.add_parser("status")
    status.add_argument("--run-id", required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        backend = None
        if args.command == "run":
            config = _json_file(args.model_config) if args.model_config else None
            backend = build_model_backend(config)
        loop = LearningLoop(
            args.data_root,
            args.state_dir,
            args.work_root,
            backend=backend,
        )
        if args.command == "run":
            result = loop.execute(_json_file(args.task_file))
        elif args.command == "review":
            result = loop.review(
                args.run_id,
                args.candidate_id,
                args.action,
                reason=args.reason,
            )
        elif args.command == "compare":
            result = loop.compare(
                args.first_run,
                args.second_run,
                minimum_improvement=args.minimum_improvement,
            )
        else:
            result = loop.status(args.run_id)
    except Exception as exc:
        print(
            json.dumps(
                {"error": {"code": "learning_loop_failed", "message": str(exc)}},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
