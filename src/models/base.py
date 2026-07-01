from __future__ import annotations


class ModelBackend:
    """Minimal model-backend contract used by LAOS.

    Backends may implement chat, review, or both. Core LAOS code depends only on
    these methods, so provider-specific authentication and SDKs stay isolated.
    """

    backend_id = "base"
    capabilities = frozenset()

    def supports(self, capability):
        return capability in self.capabilities

    def complete(self, messages, tools=None, **options):
        raise NotImplementedError("chat completion is not supported")

    def review(self, messages):
        response = self.complete(messages, response_format="json")
        if isinstance(response, str):
            return response
        if isinstance(response, dict) and isinstance(response.get("content"), str):
            return response["content"]
        raise ValueError("model backend returned an invalid review response")

    def __call__(self, messages, tools=None):
        return self.complete(messages, tools=tools)
