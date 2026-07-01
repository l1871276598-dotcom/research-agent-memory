#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

from src.diagnostics.health import HealthCheck
from src.memory import init_store
from src.profiles.store import ProfileStore


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--profile", default="personal")
    parser.add_argument("--workspace", choices=("personal", "work"), default="personal")
    parser.add_argument("--project")
    parser.add_argument(
        "--confidentiality",
        choices=("public", "personal", "internal", "restricted"),
        default="personal",
    )
    args = parser.parse_args(argv)

    data_root = Path(args.data_root).expanduser()
    state_dir = Path(args.state_dir).expanduser()
    init_store(data_root)
    state_dir.mkdir(parents=True, exist_ok=True)

    profiles = ProfileStore(state_dir)
    try:
        profile = profiles.get(args.profile)
    except ValueError:
        profile = profiles.create(
            args.profile,
            workspace=args.workspace,
            project=args.project,
            confidentiality=args.confidentiality,
        )

    health = HealthCheck(data_root, state_dir).run()
    print(
        json.dumps(
            {
                "data_root": str(data_root),
                "state_dir": str(state_dir),
                "profile": profile,
                "health": health,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return int(not health["healthy"])


if __name__ == "__main__":
    raise SystemExit(main())
