class CombinedContextBuilder:
    def __init__(self, memory_builder, procedure_builder, procedure_ratio=0.3):
        if not callable(getattr(memory_builder, "build", None)):
            raise ValueError("memory builder is invalid")
        if not callable(getattr(procedure_builder, "build", None)):
            raise ValueError("procedure builder is invalid")
        if not 0 < procedure_ratio < 1:
            raise ValueError("procedure_ratio must be between zero and one")
        self.memory_builder = memory_builder
        self.procedure_builder = procedure_builder
        self.procedure_ratio = procedure_ratio

    def build(self, query, limit=8000, workspace=None, project=None):
        procedure_limit = max(1, int(limit * self.procedure_ratio))
        memory_limit = limit - procedure_limit
        memory = self.memory_builder.build(query, memory_limit, workspace, project)
        procedures = self.procedure_builder.build(query, procedure_limit)
        parts = []
        if memory.get("text"):
            parts.append(memory["text"])
        if procedures.get("text"):
            parts.append("procedure context:\n" + procedures["text"])
        text = "\n\n".join(parts)[:limit]
        sources = list(memory.get("sources", []))
        sources.extend(f"procedure:{name}" for name in procedures.get("sources", []))
        return {"text": text, "sources": sources, "limit": limit, "used": len(text)}

    @staticmethod
    def empty(limit=8000):
        return {"text": "", "sources": [], "limit": limit, "used": 0}
