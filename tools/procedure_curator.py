#!/usr/bin/env python3

import argparse
import json
from datetime import datetime

from src.procedures.catalog import ProcedureCatalog, _stamp
from src.procedures.curator_apply import ProcedureCuratorTransitions
from src.procedures.curator_runner import ProcedureCurator


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report")
    report.add_argument("--stale-days", type=int, default=30)
    report.add_argument("--archive-days", type=int, default=90)

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--change-json", required=True)

    pin = sub.add_parser("pin")
    pin.add_argument("name")
    pin.add_argument("--off", action="store_true")

    activity = sub.add_parser("activity")
    activity.add_argument("name")
    activity.add_argument("--kind", choices=("use", "view"), default="use")

    args = parser.parse_args(argv)
    catalog = ProcedureCatalog(args.state_dir)

    if args.command == "report":
        result = ProcedureCurator(args.data_root, catalog).report(
            stale_days=args.stale_days,
            archive_days=args.archive_days,
        )
    elif args.command == "apply":
        result = ProcedureCuratorTransitions(args.data_root, catalog).apply(
            json.loads(args.change_json)
        )
    else:
        current = catalog.ensure(args.name)
        state = catalog._load()
        record = state["records"][args.name]
        if args.command == "pin":
            record["pinned"] = not args.off
            if record["pinned"]:
                record["state"] = "active"
        else:
            field = "use_count" if args.kind == "use" else "view_count"
            record[field] = current[field] + 1
            record["last_activity_at"] = _stamp()
            record["state"] = "active"
        catalog._save(state)
        result = {"name": args.name, **record}

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
