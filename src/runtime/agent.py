"""LAOS conversational agent loop.

The loop shape is selectively adapted from Hermes Agent: prompt assembly,
model call, tool dispatch, result replay, final response, and provider hooks.
Model selection/provider management is intentionally outside this module.
"""

from __future__ import annotations

import json


class AgentRuntime:
    def __init__(
        self,
        model,
        tools,
        *,
        memory=None,
        sessions=None,
        extensions=None,
        max_iterations=20,
    ):
        if not callable(model):
            raise ValueError("model must be callable")
        if not callable(getattr(tools, "execute", None)):
            raise ValueError("tool executor is invalid")
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        self.model = model
        self.tools = tools
        self.memory = memory
        self.sessions = sessions
        self.extensions = extensions
        self.max_iterations = max_iterations

    def _messages(self, session_id, system_prompt, user_message):
        messages = []
        blocks = [system_prompt.strip()] if isinstance(system_prompt, str) and system_prompt.strip() else []
        if self.memory is not None:
            static = self.memory.system_prompt_block()
            recall = self.memory.prefetch(user_message, session_id=session_id)
            if static:
                blocks.append(static)
            if recall:
                blocks.append(recall)
        if blocks:
            messages.append({"role": "system", "content": "\n\n".join(blocks)})
        if self.sessions is not None:
            try:
                history = self.sessions.get(session_id)["messages"]
            except ValueError:
                history = []
            messages.extend(
                {"role": item["role"], "content": item["content"]}
                for item in history
            )
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _response(value):
        if not isinstance(value, dict):
            raise ValueError("model response must be an object")
        content = value.get("content", "")
        calls = value.get("tool_calls", [])
        if not isinstance(content, str) or not isinstance(calls, list):
            raise ValueError("model response has invalid content or tool_calls")
        return content, calls

    def run(self, *, session_id, user_message, system_prompt=""):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be a non-empty string")

        if self.sessions is not None:
            try:
                self.sessions.get(session_id, include_messages=False)
            except ValueError:
                self.sessions.create(session_id=session_id, workspace="personal")
            self.sessions.append(session_id, "user", user_message)

        messages = self._messages(session_id, system_prompt, user_message)
        for iteration in range(1, self.max_iterations + 1):
            if self.extensions is not None:
                self.extensions.invoke(
                    "pre_llm_call",
                    session_id=session_id,
                    messages=messages,
                    iteration=iteration,
                )
            response = self.model(messages, self.tools.registry.definitions(self.tools.allowed))
            content, calls = self._response(response)
            if self.extensions is not None:
                self.extensions.invoke(
                    "post_llm_call",
                    session_id=session_id,
                    response=response,
                    iteration=iteration,
                )

            if not calls:
                if self.sessions is not None:
                    self.sessions.append(session_id, "assistant", content)
                if self.memory is not None:
                    self.memory.after_turn(
                        user_message,
                        content,
                        session_id=session_id,
                        messages=messages + [{"role": "assistant", "content": content}],
                    )
                return {
                    "session_id": session_id,
                    "content": content,
                    "iterations": iteration,
                }

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls,
                }
            )
            if self.sessions is not None:
                self.sessions.append(
                    session_id,
                    "assistant",
                    content,
                    {"tool_calls": calls},
                )

            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    raise ValueError("tool call is invalid")
                name = function.get("name")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                result = self.tools.execute(name, arguments)
                text = json.dumps(result, ensure_ascii=False, default=str)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": text,
                    }
                )
                if self.sessions is not None:
                    self.sessions.append(
                        session_id,
                        "tool",
                        text,
                        {"tool_call_id": call.get("id", ""), "tool": name},
                    )

        raise RuntimeError("agent reached the maximum tool iterations")
