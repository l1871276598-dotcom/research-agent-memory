#!/usr/bin/env python3

import argparse
import json

from src.sessions import SessionStore


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--session-id")
    create.add_argument("--workspace", default="personal")
    create.add_argument("--project")
    create.add_argument("--title")

    append = sub.add_parser("append")
    append.add_argument("session_id")
    append.add_argument("role")
    append.add_argument("content")

    get = sub.add_parser("get")
    get.add_argument("session_id")

    listing = sub.add_parser("list")
    listing.add_argument("--workspace")
    listing.add_argument("--project")

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--workspace")
    search.add_argument("--project")

    end = sub.add_parser("end")
    end.add_argument("session_id")
    end.add_argument("--reason", default="completed")

    branch = sub.add_parser("branch")
    branch.add_argument("session_id")
    branch.add_argument("--new-session-id")
    branch.add_argument("--title")

    args = parser.parse_args(argv)
    store = SessionStore(args.state_dir)

    if args.command == "create":
        result = store.create(
            session_id=args.session_id,
            workspace=args.workspace,
            project=args.project,
            title=args.title,
        )
    elif args.command == "append":
        result = store.append(args.session_id, args.role, args.content)
    elif args.command == "get":
        result = store.get(args.session_id)
    elif args.command == "list":
        result = {"sessions": store.list(workspace=args.workspace, project=args.project)}
    elif args.command == "search":
        result = {"results": store.search(args.query, workspace=args.workspace, project=args.project)}
    elif args.command == "end":
        result = store.end(args.session_id, args.reason)
    else:
        result = store.branch(
            args.session_id,
            new_session_id=args.new_session_id,
            title=args.title,
        )

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
