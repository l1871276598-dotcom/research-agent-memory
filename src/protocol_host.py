from __future__ import annotations


def build_protocol_host(facade):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("optional MCP SDK is required") from exc

    host = FastMCP(
        "laos",
        instructions="Read and update LAOS only through reviewed native operations.",
    )

    @host.tool()
    def laos_task(task: dict) -> dict:
        return facade.run_task(task)

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
