#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from src.memory import init_store


MANIFEST_NAME = "mcp_checkpoint_trial.json"


def _stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_tool(name, filename):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_path(state_dir):
    return Path(state_dir).expanduser() / MANIFEST_NAME


def _write_manifest(state_dir, manifest):
    _manifest_path(state_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _normalize_mcp_path(value):
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("mcp_path must start with /")
    if value != "/":
        value = value.rstrip("/")
    return value


def prepare_trial(
    data_root,
    state_dir,
    *,
    workspace,
    expected_checkpoints=5,
    project="laos-checkpoint-test",
    profile="personal",
    confidentiality="personal",
    port=8766,
    mcp_path="/mcp",
):
    if workspace not in {"personal", "work"}:
        raise ValueError("workspace must be personal or work")
    if confidentiality not in {"public", "personal", "internal", "restricted"}:
        raise ValueError("invalid confidentiality")
    if (
        isinstance(expected_checkpoints, bool)
        or not isinstance(expected_checkpoints, int)
        or expected_checkpoints <= 0
    ):
        raise ValueError("expected_checkpoints must be a positive integer")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    mcp_path = _normalize_mcp_path(mcp_path)

    data_root = Path(data_root).expanduser()
    state_dir = Path(state_dir).expanduser()
    init_store(data_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    local_mcp_url = f"http://127.0.0.1:{port}{mcp_path}"
    manifest = {
        "schema_version": 1,
        "trial_id": f"mcp-trial-{secrets.token_hex(6)}",
        "status": "prepared",
        "prepared_at": _stamp(),
        "decided_at": None,
        "data_root": str(data_root),
        "state_dir": str(state_dir),
        "workspace": workspace,
        "project": project,
        "profile": profile,
        "confidentiality": confidentiality,
        "expected_checkpoints": expected_checkpoints,
        "model_review_enabled": False,
        "transport": "streamable-http",
        "host": "127.0.0.1",
        "port": port,
        "mcp_path": mcp_path,
        "local_mcp_url": local_mcp_url,
        "requires_remote_tunnel": True,
        "classification": None,
        "manual_evidence": None,
    }
    _write_manifest(state_dir, manifest)
    return {
        "manifest": manifest,
        "manifest_path": str(_manifest_path(state_dir)),
        "local_mcp_url": local_mcp_url,
        "connection_requirement": (
            "ChatGPT cannot connect to this loopback URL directly. Connect it through "
            "Secure MCP Tunnel or another authenticated remote MCP endpoint."
        ),
        "chatgpt_instruction": (
            "After preparing each completed response, call laos_capture_checkpoint exactly once. "
            "Keep one stable session_alias for this conversation. Submit the current user message "
            "verbatim and assistant_response exactly as it will be shown. Use a new stable "
            "checkpoint_id for each exchange. Do not invent source IDs and do not force review."
        ),
        "next_command": (
            "python3 tools/mcp_checkpoint_trial.py serve "
            f"--state-dir {json.dumps(str(state_dir))}"
        ),
    }


def load_manifest(state_dir):
    path = _manifest_path(state_dir)
    if not path.is_file():
        raise ValueError("checkpoint trial manifest is missing; run prepare first")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("checkpoint trial manifest is invalid")
    return value


def serve_trial(state_dir):
    manifest = load_manifest(state_dir)
    serve_laos = _load_tool("serve_laos", "serve_laos.py")
    argv = [
        "--data-root",
        manifest["data_root"],
        "--state-dir",
        manifest["state_dir"],
        "--transport",
        manifest["transport"],
        "--host",
        manifest["host"],
        "--port",
        str(manifest["port"]),
        "--mcp-path",
        manifest["mcp_path"],
        "--enable-checkpoint-capture",
        "--checkpoint-account-id",
        manifest["profile"],
        "--checkpoint-workspace",
        manifest["workspace"],
        "--checkpoint-confidentiality",
        manifest["confidentiality"],
    ]
    if manifest.get("project"):
        argv.extend(["--checkpoint-project", manifest["project"]])
    if manifest.get("profile"):
        argv.extend(["--checkpoint-profile", manifest["profile"]])
    return serve_laos.main(argv)


def report_trial(state_dir):
    manifest = load_manifest(state_dir)
    validation = _load_tool("checkpoint_validation", "checkpoint_validation.py")
    report = validation.build_report(
        manifest["state_dir"],
        expected_checkpoints=manifest["expected_checkpoints"],
    )
    pending_classification = (
        "explicit_checkpoint_candidate"
        if report["checkpoint_capture_ready"]
        else "not_ready"
    )
    classification = (
        manifest.get("classification")
        if manifest.get("status") == "decided"
        else pending_classification
    )
    return {
        "trial_id": manifest["trial_id"],
        "classification": classification,
        "local_mcp_url": manifest["local_mcp_url"],
        "requires_remote_tunnel": manifest["requires_remote_tunnel"],
        "automated_report": report,
        "empirical_decision_pending": manifest.get("status") != "decided",
        "required_manual_evidence": manifest.get("manual_evidence")
        or {
            "tool_called_every_expected_turn": None,
            "assistant_hash_matches_final_display_every_turn": None,
            "write_confirmation_burden_acceptable": None,
        },
        "decision_rule": (
            "Classify MCP as a formal checkpoint channel only after the automated report passes "
            "and all three manual evidence fields are confirmed true. Never classify it as "
            "passive or lossless transcript capture."
        ),
    }


def decide_trial(
    state_dir,
    *,
    tool_called_every_expected_turn,
    assistant_hash_matches_final_display_every_turn,
    write_confirmation_burden_acceptable,
):
    evidence = {
        "tool_called_every_expected_turn": bool(tool_called_every_expected_turn),
        "assistant_hash_matches_final_display_every_turn": bool(
            assistant_hash_matches_final_display_every_turn
        ),
        "write_confirmation_burden_acceptable": bool(
            write_confirmation_burden_acceptable
        ),
    }
    result = report_trial(state_dir)
    automated_ready = result["automated_report"]["checkpoint_capture_ready"]
    if automated_ready and all(evidence.values()):
        classification = "formal_explicit_checkpoint_channel"
        accepted = True
    elif not automated_ready:
        classification = "insufficient_automated_evidence"
        accepted = False
    else:
        classification = "rejected_for_formal_checkpoint_channel"
        accepted = False

    manifest = load_manifest(state_dir)
    manifest.update(
        {
            "status": "decided",
            "decided_at": _stamp(),
            "classification": classification,
            "manual_evidence": evidence,
        }
    )
    _write_manifest(state_dir, manifest)
    return {
        "trial_id": manifest["trial_id"],
        "accepted": accepted,
        "classification": classification,
        "manual_evidence": evidence,
        "automated_report": result["automated_report"],
        "lossless_conversation_capture_proven": False,
        "scope": (
            "explicit checkpoint synchronization only"
            if accepted
            else "not approved as a formal checkpoint channel"
        ),
    }


def _yes_no(value):
    return value == "yes"


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--data-root", required=True)
    prepare.add_argument("--state-dir", required=True)
    prepare.add_argument("--workspace", choices=("personal", "work"), required=True)
    prepare.add_argument("--project", default="laos-checkpoint-test")
    prepare.add_argument("--profile", default="personal")
    prepare.add_argument(
        "--confidentiality",
        choices=("public", "personal", "internal", "restricted"),
        default="personal",
    )
    prepare.add_argument("--expected-checkpoints", type=int, default=5)
    prepare.add_argument("--port", type=int, default=8766)
    prepare.add_argument("--mcp-path", default="/mcp")

    serve = commands.add_parser("serve")
    serve.add_argument("--state-dir", required=True)

    report = commands.add_parser("report")
    report.add_argument("--state-dir", required=True)

    decide = commands.add_parser("decide")
    decide.add_argument("--state-dir", required=True)
    decide.add_argument("--tool-called-every-turn", choices=("yes", "no"), required=True)
    decide.add_argument("--hashes-match", choices=("yes", "no"), required=True)
    decide.add_argument(
        "--confirmation-burden-acceptable",
        choices=("yes", "no"),
        required=True,
    )

    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare_trial(
            args.data_root,
            args.state_dir,
            workspace=args.workspace,
            expected_checkpoints=args.expected_checkpoints,
            project=args.project,
            profile=args.profile,
            confidentiality=args.confidentiality,
            port=args.port,
            mcp_path=args.mcp_path,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "serve":
        return serve_trial(args.state_dir)
    if args.command == "report":
        result = report_trial(args.state_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return int(not result["automated_report"]["checkpoint_capture_ready"])

    result = decide_trial(
        args.state_dir,
        tool_called_every_expected_turn=_yes_no(args.tool_called_every_turn),
        assistant_hash_matches_final_display_every_turn=_yes_no(args.hashes_match),
        write_confirmation_burden_acceptable=_yes_no(
            args.confirmation_burden_acceptable
        ),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(not result["accepted"])


if __name__ == "__main__":
    raise SystemExit(main())
