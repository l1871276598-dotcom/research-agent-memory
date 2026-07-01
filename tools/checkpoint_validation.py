#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path


def build_report(state_dir, expected_checkpoints=None):
    path = Path(state_dir).expanduser() / "bridge_events.sqlite"
    if not path.is_file():
        raise ValueError("bridge event database is missing")
    if expected_checkpoints is not None and (
        isinstance(expected_checkpoints, bool)
        or not isinstance(expected_checkpoints, int)
        or expected_checkpoints <= 0
    ):
        raise ValueError("expected_checkpoints must be a positive integer")

    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT event_id,event_type,role,status,attempts,error,metadata_json
            FROM bridge_events
            WHERE source='chatgpt-mcp'
            ORDER BY id
            """
        ).fetchall()

    checkpoints = defaultdict(
        lambda: {
            "roles": set(),
            "statuses": [],
            "attempts": 0,
            "errors": [],
            "source_ids": {},
        }
    )
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        checkpoint_id = metadata.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            checkpoint_id = row["event_id"]
        item = checkpoints[checkpoint_id]
        if row["event_type"] == "message" and row["role"]:
            item["roles"].add(row["role"])
        item["statuses"].append(row["status"])
        item["attempts"] += int(row["attempts"])
        if row["error"]:
            item["errors"].append(row["error"])
        source_ids = metadata.get("source_ids")
        if isinstance(source_ids, dict):
            for key in ("conversation", "user_message", "assistant_message"):
                if source_ids.get(key):
                    item["source_ids"][key] = True

    complete = 0
    processed = 0
    failed = 0
    stable_ids = 0
    source_id_coverage = {
        "conversation": 0,
        "user_message": 0,
        "assistant_message": 0,
    }
    details = []
    for checkpoint_id, item in sorted(checkpoints.items()):
        is_complete = {"user", "assistant"} <= item["roles"] and all(
            status == "processed" for status in item["statuses"]
        )
        complete += int(is_complete)
        processed += sum(status == "processed" for status in item["statuses"])
        failed += sum(status == "failed" for status in item["statuses"])
        stable_ids += int(not checkpoint_id.startswith("derived-"))
        for key in source_id_coverage:
            source_id_coverage[key] += int(bool(item["source_ids"].get(key)))
        details.append(
            {
                "checkpoint_id": checkpoint_id,
                "complete": is_complete,
                "roles": sorted(item["roles"]),
                "statuses": item["statuses"],
                "attempts": item["attempts"],
                "errors": item["errors"],
                "source_ids": sorted(item["source_ids"]),
            }
        )

    observed = len(checkpoints)
    expected_met = expected_checkpoints is None or observed >= expected_checkpoints
    all_complete = observed > 0 and complete == observed and failed == 0
    checkpoint_ready = expected_met and all_complete
    return {
        "source": "chatgpt-mcp",
        "expected_checkpoints": expected_checkpoints,
        "observed_checkpoints": observed,
        "complete_checkpoints": complete,
        "total_events": len(rows),
        "processed_events": processed,
        "failed_events": failed,
        "stable_checkpoint_ids": stable_ids,
        "source_id_coverage": source_id_coverage,
        "checkpoint_capture_ready": checkpoint_ready,
        "lossless_conversation_capture_proven": False,
        "manual_checks_required": [
            "Confirm ChatGPT invoked the tool for every expected turn.",
            "Compare each submitted assistant-response hash with the final displayed response.",
            "Record whether ChatGPT required a write confirmation for each invocation.",
        ],
        "interpretation": (
            "MCP can be accepted only as explicit checkpoint synchronization. "
            "This report cannot prove passive or lossless conversation capture."
        ),
        "checkpoints": details,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--expected-checkpoints", type=int)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.state_dir, args.expected_checkpoints)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return int(not report["checkpoint_capture_ready"])


if __name__ == "__main__":
    raise SystemExit(main())
