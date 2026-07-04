from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ModelBackend


class OpenAICompatibleBackend(ModelBackend):
    """OpenAI-compatible chat backend using only the Python standard library.

    This adapter can target OpenAI-compatible services such as OpenAI, Ollama,
    LM Studio, vLLM, or a compatible gateway. Secrets may be supplied directly
    by trusted code or read from a named environment variable.
    """

    backend_id = "openai_compatible"
    capabilities = frozenset({"chat", "review"})

    def __init__(
        self,
        *,
        base_url,
        model,
        api_key=None,
        api_key_env=None,
        timeout=120.0,
        extra_headers=None,
        opener=urlopen,
    ):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.api_key = api_key or (os.getenv(api_key_env) if api_key_env else None)
        self.timeout = float(timeout)
        self.extra_headers = dict(extra_headers or {})
        self.opener = opener

    def _request(self, payload):
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"model backend HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"model backend request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("model backend response must be an object")
        return value

    def complete(self, messages, tools=None, **options):
        if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
            raise ValueError("messages must be a list of objects")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": options.get("temperature", 0),
        }
        if tools:
            payload["tools"] = tools
        if options.get("response_format") == "json":
            payload["response_format"] = {"type": "json_object"}
        value = self._request(payload)
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("model backend returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ValueError("model backend returned no message")
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if not isinstance(content, str) or not isinstance(tool_calls, list):
            raise ValueError("model backend returned an invalid message")
        return {"content": content, "tool_calls": tool_calls}
