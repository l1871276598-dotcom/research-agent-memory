from runtime.codex import CodexConversationReviewer

from .base import ModelBackend


class CodexReviewBackend(ModelBackend):
    backend_id = "codex"
    capabilities = frozenset({"review"})

    def __init__(self, **options):
        self.reviewer = CodexConversationReviewer(**options)

    def review(self, messages):
        return self.reviewer(messages)
