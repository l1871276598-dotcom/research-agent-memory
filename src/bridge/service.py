from __future__ import annotations

import hmac
import json

from .recovery import recover_processing


class BridgeIngestService:
    """Validate authenticated bridge payloads before durable ingestion."""

    def __init__(
        self,
        inbox,
        *,
        token,
        allowed_sources,
        max_payload_bytes=1_000_000,
    ):
        if not callable(getattr(inbox, "ingest", None)):
            raise ValueError("bridge inbox is invalid")
        if not isinstance(token, str) or len(token) < 16:
            raise ValueError("bridge token must contain at least 16 characters")
        if (
            isinstance(max_payload_bytes, bool)
            or not isinstance(max_payload_bytes, int)
            or max_payload_bytes <= 0
        ):
            raise ValueError("max_payload_bytes must be positive")
        sources = {str(item).strip().lower() for item in (allowed_sources or []) if str(item).strip()}
        if not sources:
            raise ValueError("allowed_sources must contain at least one source")
        self.inbox = inbox
        self.token = token
        self.allowed_sources = sources
        self.max_payload_bytes = max_payload_bytes

    def ingest_json(self, payload, *, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            raise PermissionError("bridge authentication failed")
        if isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raise ValueError("bridge payload must be JSON text or bytes")
        if len(raw) > self.max_payload_bytes:
            raise ValueError("bridge payload is too large")
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("bridge payload must be valid UTF-8 JSON") from exc
        if not isinstance(event, dict):
            raise ValueError("bridge payload must contain one event object")
        source = str(event.get("source", "")).strip().lower()
        if source not in self.allowed_sources:
            raise PermissionError("bridge source is not allowed")
        result = self.inbox.ingest(event)
        return {
            "event_id": result["event_id"],
            "status": result["ingest_status"],
            "queue_status": result["status"],
        }


class BridgePipeline:
    """Small orchestration surface for ingesting and projecting bridge events."""

    def __init__(self, service, projector):
        if not callable(getattr(service, "ingest_json", None)):
            raise ValueError("bridge ingestion service is invalid")
        if not callable(getattr(projector, "drain", None)):
            raise ValueError("bridge projector is invalid")
        self.service = service
        self.projector = projector

    def ingest(self, payload, *, token, process=True, limit=100):
        accepted = self.service.ingest_json(payload, token=token)
        projected = self.projector.drain(limit) if process else []
        return {"accepted": accepted, "projected": projected}

    def recover(self, *, drain=True, limit=100):
        count = recover_processing(self.service.inbox)
        projected = self.projector.drain(limit) if drain else []
        return {"requeued": count, "projected": projected}
