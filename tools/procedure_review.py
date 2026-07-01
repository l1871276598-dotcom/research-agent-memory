#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

from src.procedures.manager import ProcedureManager
from src.procedures.proposals import ProcedureProposalStore


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list")
    listing.add_argument("--status", default="candidate")

    review = sub.add_parser("review")
    review.add_argument("proposal_id")
    review.add_argument("action", choices=("accept", "reject"))
    review.add_argument("--reason")

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("proposal_id")

    rollback = sub.add_parser("rollback")
    rollback.add_argument("proposal_id")

    args = parser.parse_args(argv)
    proposals = ProcedureProposalStore(args.state_dir)
    manager = ProcedureManager(args.data_root, proposals)

    if args.command == "list":
        result = {"proposals": proposals.list(args.status)}
    elif args.command == "review":
        result = proposals.review(args.proposal_id, args.action, args.reason)
    elif args.command == "apply":
        result = manager.apply(args.proposal_id)
    else:
        result = manager.rollback(args.proposal_id)

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
