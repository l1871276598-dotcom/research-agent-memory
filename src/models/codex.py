from runtime.codex import CodexConversationReviewer

from .base import ModelBackend


class CodexReviewBackend(ModelBackend):
    backend_id = "codex"
    capabilities = frozenset({"chat", "review"})

    def __init__(self, **options):
        self.reviewer = CodexConversationReviewer(**options)
        self.executor = CodexConversationReviewer(
            **options,
            json_only=False,
        )

    def complete(self, messages, tools=None, **options):
        if tools:
            raise ValueError("Codex read-only backend does not accept tools")
        return {"content": self.executor(messages), "tool_calls": []}

    def review(self, messages):
        return self.reviewer(messages)
