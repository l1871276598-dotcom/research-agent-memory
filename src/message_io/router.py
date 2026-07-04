"""Authenticated multi-platform message router adapted from Hermes gateway patterns."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from .types import MessageEvent


class AccessPolicy:
    def __init__(self, allowed=None, *, default_allow=False):
        self.allowed = {
            str(platform).lower(): {str(user) for user in users}
            for platform, users in (allowed or {}).items()
        }
        self.default_allow = bool(default_allow)

    def allows(self, source):
        users = self.allowed.get(source.platform.lower())
        if users is None:
            return self.default_allow
        return source.user_id in users


class DeliveryLedger:
    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "message_delivery.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_messages (
                    platform TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    PRIMARY KEY(platform, message_id)
                )
                """
            )
            connection.commit()

    def claim(self, event, session_id):
        with closing(sqlite3.connect(self.path)) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO inbound_messages(platform,message_id,session_id) VALUES(?,?,?)",
                (event.source.platform, event.message_id, session_id),
            )
            connection.commit()
            return cursor.rowcount == 1


class GatewayRouter:
    def __init__(
        self,
        adapters,
        handler,
        *,
        state_dir,
        access=None,
        extensions=None,
        max_input_chars=50_000,
    ):
        if not callable(handler):
            raise ValueError("message handler must be callable")
        if isinstance(max_input_chars, bool) or not isinstance(max_input_chars, int) or max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        self.adapters = adapters
        self.handler = handler
        self.access = access or AccessPolicy()
        self.extensions = extensions
        self.ledger = DeliveryLedger(state_dir)
        self.max_input_chars = max_input_chars

    @staticmethod
    def session_id(source):
        digest = hashlib.sha256(source.session_key.encode("utf-8")).hexdigest()[:24]
        return f"message-{digest}"

    def route(self, event):
        if not isinstance(event, MessageEvent):
            raise ValueError("message event is invalid")
        if not self.access.allows(event.source):
            return {"status": "denied", "message_id": event.message_id}
        if len(event.text) > self.max_input_chars:
            return {"status": "rejected", "reason": "message_too_large"}

        session_id = self.session_id(event.source)
        if not self.ledger.claim(event, session_id):
            return {"status": "duplicate", "session_id": session_id}

        if self.extensions is not None:
            decisions = self.extensions.invoke(
                "pre_gateway_dispatch",
                event=event,
                session_id=session_id,
            )
            for decision in decisions:
                if isinstance(decision, dict) and decision.get("action") == "deny":
                    return {
                        "status": "denied",
                        "reason": decision.get("reason", "extension_policy"),
                    }

        response = self.handler(
            text=event.text,
            session_id=session_id,
            source=event.source,
            attachments=list(event.attachments),
            metadata=dict(event.metadata),
        )
        if isinstance(response, dict):
            text = response.get("content", "")
        else:
            text = str(response)
        adapter = self.adapters.get(event.source.platform)
        delivered = adapter.send_text(
            event.source,
            text,
            reply_to=event.message_id,
            metadata={"notify": True, "session_id": session_id},
        )
        return {
            "status": "delivered",
            "session_id": session_id,
            "deliveries": delivered,
        }
