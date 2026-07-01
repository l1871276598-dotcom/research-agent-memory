class ProviderRuntime:
    def __init__(self, provider):
        self.provider = provider
        self.ready = False

    def initialize(self, session_id, **kwargs):
        if not self.provider.is_available():
            raise RuntimeError("memory provider is unavailable")
        self.provider.initialize(session_id, **kwargs)
        self.ready = True

    def prefetch(self, query, session_id=""):
        return self.provider.prefetch(query, session_id=session_id) if self.ready else ""

    def after_turn(self, user, assistant, session_id="", messages=None):
        if not self.ready:
            return None
        value = self.provider.sync_turn(
            user, assistant, session_id=session_id, messages=messages
        )
        self.provider.queue_prefetch(user, session_id=session_id)
        return value

    def close(self):
        if self.ready:
            self.provider.shutdown()
        self.ready = False
