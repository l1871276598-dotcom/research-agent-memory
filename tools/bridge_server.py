#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from src.bridge import build_bridge_pipeline
from src.models import build_model_backend


def _json(path):
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("configuration must be a JSON object")
    return value


def _token(args):
    if args.token_file:
        value = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        value = os.getenv(args.token_env, "").strip()
    if len(value) < 16:
        raise ValueError("bridge token is missing or too short")
    return value


def build_pipeline(args):
    model_config = _json(args.model_config)
    backend = build_model_backend(model_config) if model_config else None
    scopes = _json(args.scope_config) or {}
    allowed_sources = scopes.get("allowed_sources")
    mappings = scopes.get("mappings", {})
    default_scope = scopes.get("default")
    return build_bridge_pipeline(
        args.data_root,
        args.state_dir,
        token=_token(args),
        model_backend=backend,
        scope_mappings=mappings,
        default_scope=default_scope,
        allowed_sources=allowed_sources,
        memory_interval=args.memory_interval,
        skill_interval=args.skill_interval,
        max_payload_bytes=args.max_payload_bytes,
    )


def handler_factory(pipeline, token, max_payload_bytes):
    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "LAOSBridge/0.1"

        def _write(self, status, value):
            body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                self._write(404, {"error": "not_found"})
                return
            self._write(
                200,
                {
                    "healthy": True,
                    "review_enabled": pipeline.coordinator is not None,
                    "bind": "127.0.0.1",
                },
            )

        def do_POST(self):
            if self.path != "/events":
                self._write(404, {"error": "not_found"})
                return
            supplied = self.headers.get("X-LAOS-Bridge-Token", "")
            if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):
                self._write(401, {"error": "unauthorized"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._write(415, {"error": "application_json_required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if length < 0:
                self._write(411, {"error": "content_length_required"})
                return
            if length > max_payload_bytes:
                self._write(413, {"error": "payload_too_large"})
                return
            payload = self.rfile.read(length)
            try:
                result = pipeline.ingest(payload, token=supplied)
            except PermissionError as exc:
                self._write(401, {"error": "unauthorized", "message": str(exc)})
                return
            except ValueError as exc:
                self._write(400, {"error": "invalid_event", "message": str(exc)})
                return
            except Exception:
                self._write(500, {"error": "bridge_failed"})
                return
            self._write(202, result)

        def log_message(self, format, *args):
            return

    return BridgeHandler


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-env", default="LAOS_BRIDGE_TOKEN")
    parser.add_argument("--token-file")
    parser.add_argument("--model-config")
    parser.add_argument("--scope-config")
    parser.add_argument("--memory-interval", type=int, default=10)
    parser.add_argument("--skill-interval", type=int, default=10)
    parser.add_argument("--max-payload-bytes", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    pipeline = build_pipeline(args)
    pipeline.recover(drain=True)
    token = _token(args)
    handler = handler_factory(pipeline, token, args.max_payload_bytes)
    server = HTTPServer(("127.0.0.1", args.port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
