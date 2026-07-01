#!/usr/bin/env python3

import argparse

from src.laos import build_application
from src.procedures.manager import ProcedureManager
from src.procedures.proposals import ProcedureProposalStore
from src.protocol_host import build_protocol_host
from src.sessions import SessionStore
from src.tool_facade import LaosToolFacade


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)

    proposals = ProcedureProposalStore(args.state_dir)
    facade = LaosToolFacade(
        build_application(args.data_root, args.state_dir),
        sessions=SessionStore(args.state_dir),
        procedures=proposals,
        manager=ProcedureManager(args.data_root, proposals),
    )
    build_protocol_host(facade).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
