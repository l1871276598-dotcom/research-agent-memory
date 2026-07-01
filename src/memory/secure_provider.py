from .provider import LaosMemoryProvider
from .render import render_recall


class FencedLaosMemoryProvider(LaosMemoryProvider):
    def prefetch(self, query, *, session_id=""):
        result = self.builder.build(
            query,
            self.context_limit,
            self.workspace,
            self.project,
        )
        return render_recall(result["text"])


__all__ = ["FencedLaosMemoryProvider"]
