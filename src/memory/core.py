class MemoryCore:
    def __init__(self, store, candidates):
        if not callable(getattr(store, "get", None)) or not callable(
            getattr(store, "active_relevant", None)
        ):
            raise ValueError("memory store is invalid")
        if not callable(getattr(candidates, "create", None)):
            raise ValueError("candidate store is invalid")
        self.store = store
        self.candidates = candidates

    def create_candidate(self, values):
        return self.candidates.create(values)

    def get(self, memory_id):
        return self.store.get(memory_id)

    def search(self, query, workspace=None, project=None):
        return self.store.active_relevant(query, workspace, project)
