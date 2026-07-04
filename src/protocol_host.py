from __future__ import annotations


def build_protocol_host(facade, *, server_options=None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("optional MCP SDK is required") from exc

    checkpoint_enabled = getattr(facade, "checkpoint_capture", None) is not None
    instructions = "Read and update LAOS only through reviewed native operations."
    if checkpoint_enabled:
        instructions += (
            " The checkpoint tool is explicit, not passive: call it only when the "
            "current user message and the exact completed assistant response are available."
        )
    host = FastMCP("laos", instructions=instructions, **dict(server_options or {}))

    @host.tool()
    def laos_task(task: dict) -> dict:
        return facade.run_task(task)

    if checkpoint_enabled:

        @host.tool()
        def laos_capture_checkpoint(
            session_alias: str,
            user_message: str,
            assistant_response: str,
            checkpoint_id: str | None = None,
            conversation_summary: str | None = None,
            assistant_response_complete: bool = True,
            source_conversation_id: str | None = None,
            source_user_message_id: str | None = None,
            source_assistant_message_id: str | None = None,
            branch_id: str = "main",
            version: int = 1,
            captured_at: str | None = None,
            force_review: bool = False,
        ) -> dict:
            """Explicitly save one completed ChatGPT exchange into LAOS.

            This tool cannot inspect the conversation automatically. `user_message`
            must be the current user message and `assistant_response` must exactly
            match the completed response that will be shown to the user. Keep
            `session_alias` stable across one conversation. Supply source IDs only
            when the host exposes real stable IDs; never invent them. Reuse a stable
            `checkpoint_id` when retrying the same exchange. Set `force_review` only
            when immediate Memory and Procedure candidate review is required.
            """
            return facade.capture_checkpoint(
                session_alias=session_alias,
                user_message=user_message,
                assistant_response=assistant_response,
                checkpoint_id=checkpoint_id,
                conversation_summary=conversation_summary,
                assistant_response_complete=assistant_response_complete,
                source_conversation_id=source_conversation_id,
                source_user_message_id=source_user_message_id,
                source_assistant_message_id=source_assistant_message_id,
                branch_id=branch_id,
                version=version,
                captured_at=captured_at,
                force_review=force_review,
            )

    @host.tool()
    def laos_session_search(
        query: str,
        workspace: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return facade.session_search(query, workspace, project, limit)

    @host.tool()
    def laos_session_get(session_id: str) -> dict:
        return facade.session_get(session_id)

    @host.tool()
    def laos_procedure_list(status: str = "candidate") -> list[dict]:
        return facade.procedure_list(status)

    return host
