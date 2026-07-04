class CodexRuntime:
    def __init__(self, runner, sessions=None, memory=None):
        self.runner = runner
        self.sessions = sessions
        self.memory = memory
        self.session_id = None

    def start(self, session_id, workspace="personal"):
        self.session_id = session_id
        if self.sessions is not None:
            try:
                self.sessions.get(session_id, include_messages=False)
            except ValueError:
                self.sessions.create(session_id=session_id, workspace=workspace, source="codex")
        if self.memory is not None:
            self.memory.initialize(session_id)

    def run(self, message, system_prompt="", timeout=600):
        if not self.session_id:
            raise RuntimeError("runtime is not started")
        blocks = [system_prompt] if system_prompt else []
        if self.memory is not None:
            blocks.extend(
                value
                for value in (
                    self.memory.system_prompt_block(),
                    self.memory.prefetch(message, session_id=self.session_id),
                )
                if value
            )
        prompt = "\n\n".join(blocks + [message])
        if self.sessions is not None:
            self.sessions.append(self.session_id, "user", message)
        result = self.runner.run(prompt, timeout=timeout)
        if self.sessions is not None:
            self.sessions.append(self.session_id, "assistant", result["content"])
        if self.memory is not None:
            self.memory.after_turn(
                message,
                result["content"],
                session_id=self.session_id,
                messages=self.sessions.get(self.session_id)["messages"] if self.sessions else None,
            )
        return {"session_id": self.session_id, **result}

    def close(self):
        if self.memory is not None:
            self.memory.close()
        self.runner.close()
