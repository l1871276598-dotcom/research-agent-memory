#!/usr/bin/env python3

import argparse
import json
import secrets
from pathlib import Path

from src.bridge import McpCheckpointCapture, build_bridge_pipeline
from src.laos import build_application
from src.models import build_model_backend
from src.procedures.manager import ProcedureManager
from src.procedures.proposals import ProcedureProposalStore
from src.protocol_host import build_protocol_host
from src.sessions import SessionStore
from src.tool_facade import LaosToolFacade


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _json(path):
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("model configuration must be a JSON object")
    return value


def _mcp_path(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise argparse.ArgumentTypeError("MCP path must start with /")
    if value != "/" and value.endswith("/"):
        value = value.rstrip("/")
    return value


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--model-config")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", choices=tuple(sorted(LOOPBACK_HOSTS)), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--mcp-path", type=_mcp_path, default="/mcp")
    parser.add_argument("--enable-checkpoint-capture", action="store_true")
    parser.add_argument("--checkpoint-account-id", default="default")
    parser.add_argument("--checkpoint-workspace", choices=("personal", "work"))
    parser.add_argument("--checkpoint-project")
    parser.add_argument("--checkpoint-profile")
    parser.add_argument(
        "--checkpoint-confidentiality",
        choices=("public", "personal", "internal", "restricted"),
        default="personal",
    )
    parser.add_argument("--memory-interval", type=int, default=10)
    parser.add_argument("--skill-interval", type=int, default=10)
    return parser


def _server_options(args):
    if args.transport == "stdio":
        return None
    if args.host not in LOOPBACK_HOSTS:
        raise ValueError("Streamable HTTP must bind to a loopback host")
    if isinstance(args.port, bool) or not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return {
        "host": args.host,
        "port": args.port,
        "streamable_http_path": args.mcp_path,
        "json_response": True,
        "stateless_http": True,
    }


def build_facade(args):
    model_config = _json(args.model_config)
    backend = build_model_backend(model_config) if model_config else None
    proposals = ProcedureProposalStore(args.state_dir)
    checkpoint_capture = None
    sessions = SessionStore(args.state_dir)

    if args.enable_checkpoint_capture:
        if not args.checkpoint_workspace:
            raise ValueError(
                "--checkpoint-workspace is required when checkpoint capture is enabled"
            )
        internal_token = secrets.token_urlsafe(32)
        scope = {
            "workspace": args.checkpoint_workspace,
            "project": args.checkpoint_project,
            "profile": args.checkpoint_profile,
            "confidentiality": args.checkpoint_confidentiality,
        }
        pipeline = build_bridge_pipeline(
            args.data_root,
            args.state_dir,
            token=internal_token,
            model_backend=backend,
            allowed_sources={"chatgpt-mcp"},
            default_scope=scope,
            memory_interval=args.memory_interval,
            skill_interval=args.skill_interval,
        )
        pipeline.recover(drain=True)
        sessions = pipeline.sessions
        checkpoint_capture = McpCheckpointCapture(
            pipeline,
            token=internal_token,
            account_id=args.checkpoint_account_id,
        )

    application = build_application(args.data_root, args.state_dir, backend)
    return LaosToolFacade(
        application,
        sessions=sessions,
        procedures=proposals,
        manager=ProcedureManager(args.data_root, proposals),
        checkpoint_capture=checkpoint_capture,
    )


def build_host(args):
    return build_protocol_host(
        build_facade(args),
        server_options=_server_options(args),
    )


def main(argv=None):
    args = _parser().parse_args(argv)
    build_host(args).run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
