from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeScope:
    workspace: str
    project: str | None = None
    confidentiality: str = "personal"
    profile: str | None = None


class BridgeScopeResolver:
    """Resolve bridge account/source identities into LAOS data scopes."""

    def __init__(self, mappings=None, default=None):
        self.mappings = dict(mappings or {})
        self.default = default or BridgeScope("personal")

    @staticmethod
    def _scope(value):
        if isinstance(value, BridgeScope):
            return value
        if not isinstance(value, dict):
            raise ValueError("bridge scope must be an object")
        workspace = value.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("bridge scope workspace is required")
        project = value.get("project")
        if project is not None and (not isinstance(project, str) or not project.strip()):
            raise ValueError("bridge scope project is invalid")
        confidentiality = value.get("confidentiality", "personal")
        if confidentiality not in {"public", "personal", "internal", "restricted"}:
            raise ValueError("bridge scope confidentiality is invalid")
        profile = value.get("profile")
        if profile is not None and (not isinstance(profile, str) or not profile.strip()):
            raise ValueError("bridge scope profile is invalid")
        return BridgeScope(workspace.strip(), project, confidentiality, profile)

    def resolve(self, event):
        keys = [
            (event["source"], event["account_id"]),
            event["account_id"],
            event["source"],
        ]
        for key in keys:
            if key in self.mappings:
                return self._scope(self.mappings[key])
        return self._scope(self.default)


class SessionProjector:
    """Project finalized bridge events into SessionStore exactly once."""

    def __init__(self, inbox, sessions, *, coordinator=None, scope_resolver=None):
        if not callable(getattr(inbox, "claim_next", None)):
            raise ValueError("bridge inbox is invalid")
        for method in ("create", "append", "get"):
            if not callable(getattr(sessions, method, None)):
                raise ValueError("session store is invalid")
        if coordinator is not None and not callable(getattr(coordinator, "record_turn", None)):
            raise ValueError("conversation review coordinator is invalid")
        self.inbox = inbox
        self.sessions = sessions
        self.coordinator = coordinator
        self.scope_resolver = scope_resolver or BridgeScopeResolver()

    @staticmethod
    def session_id(event):
        value = ":".join(
            (
                event["source"],
                event["account_id"],
                event["conversation_id"],
                event["branch_id"],
            )
        )
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return f"bridge-{digest}"

    def _ensure_session(self, event, scope):
        session_id = self.session_id(event)
        try:
            current = self.sessions.get(session_id, include_messages=False)
        except ValueError:
            current = self.sessions.create(
                session_id=session_id,
                source=f"bridge:{event['source']}",
                workspace=scope.workspace,
                project=scope.project,
                title=event["metadata"].get("conversation_title"),
                metadata={
                    "bridge_source": event["source"],
                    "bridge_account_id": event["account_id"],
                    "bridge_conversation_id": event["conversation_id"],
                    "bridge_branch_id": event["branch_id"],
                    "profile": scope.profile,
                    "confidentiality": scope.confidentiality,
                },
            )
        if current["workspace"] != scope.workspace or current.get("project") != scope.project:
            raise ValueError("bridge session scope changed")
        return session_id

    def _already_projected(self, session_id, event_id):
        history = self.sessions.get(session_id)["messages"]
        return any(
            item.get("metadata", {}).get("bridge_event_id") == event_id
            for item in history
        )

    @staticmethod
    def _review_messages(messages):
        output = []
        for item in messages:
            metadata = item.get("metadata", {})
            message = {"role": item["role"], "content": item["content"]}
            if isinstance(metadata, dict):
                for key in ("tool_calls", "tool_call_id"):
                    if key in metadata:
                        message[key] = metadata[key]
            output.append(message)
        return output

    def _review(
        self,
        session_id,
        scope,
        *,
        event_key,
        force=False,
        tool_iterations=0,
    ):
        if self.coordinator is None:
            return None
        history = self.sessions.get(session_id)["messages"]
        if not history:
            return {"status": "skipped", "reason": "empty_conversation"}
        position = max(item["ordinal"] for item in history)
        return self.coordinator.record_turn(
            session_id=session_id,
            messages=self._review_messages(history),
            workspace=scope.workspace,
            confidentiality=scope.confidentiality,
            project=scope.project,
            tool_iterations=tool_iterations,
            force=force,
            turn_increment=0 if force else 1,
            event_key=event_key,
            review_position=position,
        )

    def project(self, event):
        scope = self.scope_resolver.resolve(event)
        session_id = self._ensure_session(event, scope)
        review = None
        if event["event_type"] == "message":
            already_projected = self._already_projected(session_id, event["event_id"])
            if not already_projected:
                metadata = {
                    "bridge_event_id": event["event_id"],
                    "bridge_message_id": event["message_id"],
                    "bridge_parent_message_id": event["parent_message_id"],
                    "bridge_branch_id": event["branch_id"],
                    "bridge_version": event["version"],
                    "bridge_source": event["source"],
                    "bridge_generated": event["role"] == "assistant",
                    **event["metadata"],
                }
                self.sessions.append(
                    session_id,
                    event["role"],
                    event["content"],
                    metadata,
                )
            if event["role"] == "assistant":
                tool_iterations = event["metadata"].get("tool_iterations", 0)
                if isinstance(tool_iterations, bool) or not isinstance(tool_iterations, int):
                    tool_iterations = 0
                review = self._review(
                    session_id,
                    scope,
                    event_key=event["event_id"],
                    tool_iterations=max(0, tool_iterations),
                )
        else:
            review = self._review(
                session_id,
                scope,
                event_key=event["event_id"],
                force=True,
            )
        return {
            "event_id": event["event_id"],
            "session_id": session_id,
            "event_type": event["event_type"],
            "review": review,
        }

    def process_next(self):
        event = self.inbox.claim_next()
        if event is None:
            return None
        try:
            result = self.project(event)
        except Exception as exc:
            failed = self.inbox.mark_failed(event["event_id"], str(exc))
            return {
                "event_id": event["event_id"],
                "status": failed["status"],
                "error": str(exc),
            }
        self.inbox.mark_processed(event["event_id"])
        return {"status": "processed", **result}

    def drain(self, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        results = []
        for _ in range(limit):
            result = self.process_next()
            if result is None:
                break
            results.append(result)
            if result.get("status") == "queued":
                break
        return results
