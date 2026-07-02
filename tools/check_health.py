#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from src.diagnostics.health import HealthCheck


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--model-backend", default="codex")
    args = parser.parse_args(argv)
    result = HealthCheck(
        args.data_root,
        args.state_dir,
        model_backend=args.model_backend,
    ).run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(not result["healthy"])


if __name__ == "__main__":
    raise SystemExit(main())
