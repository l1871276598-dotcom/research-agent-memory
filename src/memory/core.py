class MemoryCore:
    def __init__(self, store, candidates, document_search=None):
        if not callable(getattr(store, "get", None)) or not callable(
            getattr(store, "active_relevant", None)
        ):
            raise ValueError("memory store is invalid")
        if not callable(getattr(candidates, "create", None)):
            raise ValueError("candidate store is invalid")
        if document_search is not None and not callable(document_search):
            raise ValueError("document search is invalid")
        self.store = store
        self.candidates = candidates
        self.document_search = document_search

    def create_candidate(self, values):
        return self.candidates.create(values)

    def find_candidate_by_source_id(self, source_id):
        finder = getattr(self.candidates, "find_by_source_id", None)
        return finder(source_id) if callable(finder) else None

    def get(self, memory_id):
        return self.store.get(memory_id)

    def reviewable(self, workspace=None, project=None, statuses=None):
        finder = getattr(self.store, "reviewable", None)
        if not callable(finder):
            raise ValueError("memory store cannot list reviewable records")
        return finder(workspace, project, statuses)

    def search(self, query, workspace=None, project=None):
        results = self.store.active_relevant(query, workspace, project)
        if self.document_search is not None:
            results.extend(self.document_search(query, workspace, project))
        return results
