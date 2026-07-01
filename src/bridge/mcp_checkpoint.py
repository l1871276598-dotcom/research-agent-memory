from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value, name):
    if value is None:
        return None
    return _text(value, name)


def _stamp(value=None):
    if value is None:
        current = datetime.now(timezone.utc)
    else:
        try:
            current = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError("captured_at must be an ISO timestamp") from exc
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class McpCheckpointCapture:
    """Capture an exchange explicitly submitted through an MCP tool call.

    This service does not claim passive access to the ChatGPT conversation. Every
    field is caller-submitted and receipts expose that limitation for validation.
    """

    source = "chatgpt-mcp"

    def __init__(self, pipeline, *, token, account_id="default"):
        if not callable(getattr(pipeline, "ingest", None)):
            raise ValueError("bridge pipeline is invalid")
        if not isinstance(token, str) or len(token) < 16:
            raise ValueError("checkpoint token must contain at least 16 characters")
        self.pipeline = pipeline
        self.token = token
        self.account_id = _text(account_id, "account_id")

    @staticmethod
    def _checkpoint_id(
        session_alias,
        user_message,
        assistant_response,
        branch_id,
        version,
        supplied,
    ):
        if supplied is not None:
            return _text(supplied, "checkpoint_id")
        material = json.dumps(
            {
                "session_alias": session_alias,
                "user_message": user_message,
                "assistant_response": assistant_response,
                "branch_id": branch_id,
                "version": version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"derived-{_digest(material)[:24]}"

    def capture(
        self,
        *,
        session_alias,
        user_message,
        assistant_response,
        checkpoint_id=None,
        conversation_summary=None,
        assistant_response_complete=True,
        source_conversation_id=None,
        source_user_message_id=None,
        source_assistant_message_id=None,
        branch_id="main",
        version=1,
        captured_at=None,
        force_review=False,
    ):
        session_alias = _text(session_alias, "session_alias")
        user_message = _text(user_message, "user_message")
        assistant_response = _text(assistant_response, "assistant_response")
        conversation_summary = _optional_text(conversation_summary, "conversation_summary")
        source_conversation_id = _optional_text(
            source_conversation_id,
            "source_conversation_id",
        )
        source_user_message_id = _optional_text(
            source_user_message_id,
            "source_user_message_id",
        )
        source_assistant_message_id = _optional_text(
            source_assistant_message_id,
            "source_assistant_message_id",
        )
        branch_id = _text(branch_id, "branch_id")
        if not isinstance(assistant_response_complete, bool):
            raise ValueError("assistant_response_complete must be boolean")
        if not assistant_response_complete:
            raise ValueError("checkpoint requires a completed assistant response")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(force_review, bool):
            raise ValueError("force_review must be boolean")

        checkpoint_id = self._checkpoint_id(
            session_alias,
            user_message,
            assistant_response,
            branch_id,
            version,
            checkpoint_id,
        )
        conversation_id = source_conversation_id or session_alias
        user_id = source_user_message_id or f"{checkpoint_id}:user"
        assistant_id = source_assistant_message_id or f"{checkpoint_id}:assistant"
        captured_at = _stamp(captured_at)
        metadata = {
            "capture_mode": "explicit_mcp_checkpoint",
            "caller_submitted": True,
            "assistant_response_complete": True,
            "conversation_summary": conversation_summary,
            "source_ids": {
                "conversation": source_conversation_id,
                "user_message": source_user_message_id,
                "assistant_message": source_assistant_message_id,
            },
        }

        base = {
            "source": self.source,
            "account_id": self.account_id,
            "conversation_id": conversation_id,
            "branch_id": branch_id,
            "version": version,
            "is_final": True,
            "captured_at": captured_at,
        }
        events = [
            {
                **base,
                "event_id": f"mcp:{checkpoint_id}:user",
                "event_type": "message",
                "message_id": user_id,
                "parent_message_id": None,
                "role": "user",
                "content": user_message,
                "metadata": metadata,
            },
            {
                **base,
                "event_id": f"mcp:{checkpoint_id}:assistant",
                "event_type": "message",
                "message_id": assistant_id,
                "parent_message_id": user_id,
                "role": "assistant",
                "content": assistant_response,
                "metadata": metadata,
            },
        ]
        if force_review:
            events.append(
                {
                    "event_id": f"mcp:{checkpoint_id}:review",
                    "event_type": "checkpoint",
                    "source": self.source,
                    "account_id": self.account_id,
                    "conversation_id": conversation_id,
                    "branch_id": branch_id,
                    "captured_at": captured_at,
                    "metadata": {
                        **metadata,
                        "force_review": True,
                    },
                }
            )

        receipts = []
        projected = []
        for event in events:
            result = self.pipeline.ingest(
                json.dumps(event, ensure_ascii=False),
                token=self.token,
            )
            receipts.append(result["accepted"])
            projected.extend(result["projected"])

        session_ids = {
            item.get("session_id")
            for item in projected
            if item.get("session_id") is not None
        }
        return {
            "checkpoint_id": checkpoint_id,
            "session_id": next(iter(session_ids)) if len(session_ids) == 1 else None,
            "receipts": receipts,
            "projected": projected,
            "content_receipt": {
                "user_message_length": len(user_message),
                "user_message_sha256": _digest(user_message),
                "assistant_response_length": len(assistant_response),
                "assistant_response_sha256": _digest(assistant_response),
                "assistant_response_complete": True,
            },
            "capability_evidence": {
                "capture_mode": "explicit_tool_call",
                "passive_conversation_access": False,
                "assistant_text_independently_observed": False,
                "source_conversation_id_provided": source_conversation_id is not None,
                "source_user_message_id_provided": source_user_message_id is not None,
                "source_assistant_message_id_provided": source_assistant_message_id is not None,
                "stable_checkpoint_id_provided": not checkpoint_id.startswith("derived-"),
                "force_review": force_review,
            },
        }
