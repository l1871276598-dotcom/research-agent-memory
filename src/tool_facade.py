class LaosToolFacade:
    def __init__(
        self,
        application,
        sessions=None,
        procedures=None,
        manager=None,
        checkpoint_capture=None,
    ):
        self.application = application
        self.sessions = sessions
        self.procedures = procedures
        self.manager = manager
        self.checkpoint_capture = checkpoint_capture

    def run_task(self, task):
        return self.application.run(task)

    def memory_search(self, query, workspace, project=None, limit=20):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if workspace not in {"personal", "work"}:
            raise ValueError("workspace must be personal or work")
        if project is not None and (not isinstance(project, str) or not project.strip()):
            raise ValueError("project must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        values = {"query": query, "workspace": workspace}
        if project is not None:
            values["project"] = project
        result = self.application.run({"type": "memory.search", "input": values})
        try:
            rows = result["output"]["results"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("memory search returned an invalid result") from exc
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise RuntimeError("memory search returned an invalid result")
        return rows[:limit]

    def handoff_write(self, project_slug, content, expected_sha256=None, workspace="personal"):
        if not isinstance(project_slug, str) or not project_slug.strip():
            raise ValueError("project_slug must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not expected_sha256.strip()):
            raise ValueError("expected_sha256 must be a non-empty string")
        if workspace not in {"personal", "work"}:
            raise ValueError("workspace must be personal or work")

        values = {"project_slug": project_slug, "content": content}
        if expected_sha256 is not None:
            values["expected_sha256"] = expected_sha256
        result = self.application.run(
            {"type": "handoff.write", "workspace": workspace, "input": values}
        )
        try:
            output = result["output"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("handoff write returned an invalid result") from exc
        if not isinstance(output, dict) or output.get("project_slug") != project_slug:
            raise RuntimeError("handoff write returned an invalid result")
        return output

    def capture_checkpoint(self, **values):
        if self.checkpoint_capture is None:
            raise RuntimeError("MCP checkpoint capture is unavailable")
        return self.checkpoint_capture.capture(**values)

    def session_list(self, workspace=None, project=None, limit=50):
        if self.sessions is None:
            raise RuntimeError("session store is unavailable")
        return self.sessions.list(workspace=workspace, project=project, limit=limit)

    def session_get(self, session_id):
        if self.sessions is None:
            raise RuntimeError("session store is unavailable")
        return self.sessions.get(session_id)

    def session_search(self, query, workspace=None, project=None, limit=20):
        if self.sessions is None:
            raise RuntimeError("session store is unavailable")
        return self.sessions.search(
            query,
            workspace=workspace,
            project=project,
            limit=limit,
        )

    def procedure_list(self, status="candidate"):
        if self.procedures is None:
            raise RuntimeError("procedure proposal store is unavailable")
        return self.procedures.list(status)

    def procedure_review(self, proposal_id, action, reason=None):
        if self.procedures is None:
            raise RuntimeError("procedure proposal store is unavailable")
        return self.procedures.review(proposal_id, action, reason)

    def procedure_apply(self, proposal_id):
        if self.manager is None:
            raise RuntimeError("procedure manager is unavailable")
        return self.manager.apply(proposal_id)

    def procedure_rollback(self, proposal_id):
        if self.manager is None:
            raise RuntimeError("procedure manager is unavailable")
        return self.manager.rollback(proposal_id)
