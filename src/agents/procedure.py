from .base import BaseAgent


class ProcedureAgent(BaseAgent):
    agent_id = "procedure_agent"
    handles = []


__all__ = ["ProcedureAgent"]
