#!/usr/bin/env python3
"""Report Hermes upstream changes relevant to selectively ported LAOS components."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "hermes_upstream_components.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("components"), list):
        raise ValueError("invalid Hermes component configuration")
    return value


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "LAOS-Hermes-Upstream-Checker/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub request failed: {exc.code}: {detail[:300]}") from exc


def _matches(path: str, watched: list[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in watched)


def _risk(path: str, status: str, commit_messages: str) -> str:
    text = f"{path} {commit_messages}".lower()
    if any(word in text for word in ("security", "cve-", "credential", "token", "injection")):
        return "security"
    if status in {"removed", "renamed"}:
        return "breaking"
    if any(part in path for part in ("run_agent.py", "agent/agent_init.py", "agent/memory_manager.py", "tools/registry.py")):
        return "high"
    if any(word in text for word in ("fix", "bug", "regression")):
        return "bugfix"
    return "normal"


def _compare(config: dict[str, Any]) -> dict[str, Any]:
    upstream = config["upstream"]
    branch = config.get("branch", "main")
    baseline = config["last_reviewed_commit"]
    latest_payload = _get_json(f"https://api.github.com/repos/{upstream}/commits/{branch}")
    latest = latest_payload["sha"]

    if latest == baseline:
        files: list[dict[str, Any]] = []
        commits: list[dict[str, Any]] = []
    else:
        comparison = _get_json(
            f"https://api.github.com/repos/{upstream}/compare/{baseline}...{latest}"
        )
        files = comparison.get("files", [])
        commits = comparison.get("commits", [])

    commit_messages = "\n".join(
        str(item.get("commit", {}).get("message", "")) for item in commits if isinstance(item, dict)
    )
    relevant: list[dict[str, Any]] = []
    for component in config["components"]:
        watched = component.get("upstream_paths", [])
        changed = []
        for item in files:
            filename = str(item.get("filename", ""))
            if _matches(filename, watched):
                changed.append(
                    {
                        "path": filename,
                        "status": item.get("status"),
                        "additions": item.get("additions", 0),
                        "deletions": item.get("deletions", 0),
                        "risk": _risk(filename, str(item.get("status", "")), commit_messages),
                    }
                )
        if changed:
            relevant.append(
                {
                    "number": component["number"],
                    "id": component["id"],
                    "status": component["status"],
                    "port_mode": component["port_mode"],
                    "laos_paths": component.get("laos_paths", []),
                    "changes": changed,
                }
            )

    return {
        "upstream": upstream,
        "baseline": baseline,
        "latest": latest,
        "upstream_changed": latest != baseline,
        "changed_file_count": len(files),
        "relevant_component_count": len(relevant),
        "components": relevant,
        "action": "review_and_selectively_port" if relevant else "no_relevant_change",
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hermes upstream review",
        "",
        f"- Upstream: `{report['upstream']}`",
        f"- Baseline: `{report['baseline']}`",
        f"- Latest: `{report['latest']}`",
        f"- Relevant components: **{report['relevant_component_count']}**",
        "",
    ]
    if not report["components"]:
        lines.append("No tracked Hermes component changed.")
        return "\n".join(lines) + "\n"
    for component in report["components"]:
        lines.extend(
            [
                f"## {component['number']}. {component['id']}",
                "",
                f"Target LAOS paths: {', '.join(f'`{p}`' for p in component['laos_paths'])}",
                "",
            ]
        )
        for change in component["changes"]:
            lines.append(
                f"- `{change['path']}` — {change['status']}, risk `{change['risk']}`, "
                f"+{change['additions']}/-{change['deletions']}"
            )
        lines.append("")
    lines.append("No upstream file is copied or applied automatically. Review the diff and port selected logic into native LAOS modules.")
    return "\n".join(lines) + "\n"


def _mark_reviewed(config_path: Path, commit: str) -> dict[str, str]:
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit.lower()):
        raise ValueError("commit must be a 40-character SHA")
    config = _load(config_path)
    previous = config["last_reviewed_commit"]
    config["last_reviewed_commit"] = commit.lower()
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"previous": previous, "last_reviewed_commit": commit.lower()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--markdown", type=Path)
    mark = subparsers.add_parser("mark-reviewed")
    mark.add_argument("--commit", required=True)
    args = parser.parse_args(argv)

    if args.command == "mark-reviewed":
        result = _mark_reviewed(args.config, args.commit)
    else:
        result = _compare(_load(args.config))
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
