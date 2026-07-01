#!/usr/bin/env python3

import argparse
import json

from src.diagnostics.health import HealthCheck


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    result = HealthCheck(args.data_root, args.state_dir).run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(not result["healthy"])


if __name__ == "__main__":
    raise SystemExit(main())
