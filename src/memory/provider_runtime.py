class ProviderRuntime:
    def __init__(self, provider):
        self.provider = provider
        self.ready = False

    def initialize(self, session_id, **kwargs):
        if not self.provider.is_available():
            raise RuntimeError("memory provider is unavailable")
        self.provider.initialize(session_id, **kwargs)
        self.ready = True

    def system_prompt_block(self):
        return self.provider.system_prompt_block() if self.ready else ""

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

    def session_end(self, messages):
        return self.provider.on_session_end(messages) if self.ready else None

    def session_switch(self, session_id, **kwargs):
        if self.ready:
            return self.provider.on_session_switch(session_id, **kwargs)
        return None

    def pre_compress(self, messages):
        return self.provider.on_pre_compress(messages) if self.ready else ""

    def memory_write(self, action, target, content, metadata=None):
        if not self.ready:
            return None
        return self.provider.on_memory_write(action, target, content, metadata)

    def close(self):
        if self.ready:
            self.provider.shutdown()
        self.ready = False
