"""Isolated subagent delegation selectively adapted from Hermes Agent.

Children receive a fresh context and restricted tools. Their intermediate calls
remain private; the parent receives only bounded result summaries.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed


BLOCKED_CHILD_TOOLS = {
    "delegate_task",
    "reflection.record",
    "memory.create",
    "memory.review",
    "procedure.apply",
    "procedure.rollback",
    "send_message",
    "cronjob",
}


class DelegationManager:
    def __init__(self, child_factory, *, max_concurrent=3, max_depth=1):
        if not callable(child_factory):
            raise ValueError("child_factory must be callable")
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
            raise ValueError("max_depth must be a positive integer")
        self.child_factory = child_factory
        self.max_concurrent = max_concurrent
        self.max_depth = max_depth
        self._lock = threading.Lock()
        self._paused = False
        self._active = {}

    def pause(self, paused=True):
        with self._lock:
            self._paused = bool(paused)
            return self._paused

    def active(self):
        with self._lock:
            return [dict(value) for _, value in sorted(self._active.items())]

    @staticmethod
    def _task(value):
        if not isinstance(value, dict):
            raise ValueError("delegated task must be an object")
        goal = value.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("delegated goal must be a non-empty string")
        tools = value.get("allowed_tools", [])
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            raise ValueError("allowed_tools must be a list of strings")
        return {
            "goal": goal.strip(),
            "context": str(value.get("context", "")),
            "allowed_tools": sorted(set(tools) - BLOCKED_CHILD_TOOLS),
            "workspace": value.get("workspace", "personal"),
            "project": value.get("project"),
        }

    def _run_one(self, task, parent_id, depth):
        if depth > self.max_depth:
            raise ValueError("delegation depth limit exceeded")
        child_id = f"child-{uuid.uuid4().hex[:12]}"
        record = {
            "subagent_id": child_id,
            "parent_id": parent_id,
            "depth": depth,
            "goal": task["goal"],
            "status": "running",
            "started_at": time.time(),
        }
        with self._lock:
            self._active[child_id] = record
        try:
            child = self.child_factory(
                allowed_tools=task["allowed_tools"],
                workspace=task["workspace"],
                project=task["project"],
                depth=depth,
            )
            run = getattr(child, "run", None)
            if not callable(run):
                raise ValueError("child runtime is invalid")
            result = run(
                session_id=child_id,
                user_message=task["goal"],
                system_prompt=(
                    "You are an isolated LAOS subagent. Complete only the delegated "
                    "goal using the supplied context and allowed tools. Do not delegate, "
                    "write long-term memory, modify procedures, schedule work, or contact users.\n\n"
                    + task["context"]
                ),
            )
            summary = {
                "subagent_id": child_id,
                "status": "completed",
                "content": str(result.get("content", ""))[:12000],
                "iterations": result.get("iterations"),
            }
        except Exception as exc:
            summary = {
                "subagent_id": child_id,
                "status": "failed",
                "error": str(exc)[:2000],
            }
        finally:
            with self._lock:
                self._active.pop(child_id, None)
        return summary

    def delegate(self, tasks, *, parent_id="parent", depth=1):
        with self._lock:
            if self._paused:
                raise RuntimeError("subagent spawning is paused")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("tasks must be a non-empty list")
        validated = [self._task(task) for task in tasks]
        with ThreadPoolExecutor(max_workers=self.max_concurrent, thread_name_prefix="laos-child") as pool:
            futures = [pool.submit(self._run_one, task, parent_id, depth) for task in validated]
            return [future.result() for future in as_completed(futures)]
