from concurrent.futures import ThreadPoolExecutor


class ReviewExecutor:
    def __init__(self, coordinator):
        if not callable(getattr(coordinator, "record_turn", None)):
            raise ValueError("invalid coordinator")
        self.coordinator = coordinator
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laos-review")
        self.futures = {}

    def submit(self, **turn):
        session_id = turn.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        current = self.futures.get(session_id)
        if current is not None and not current.done():
            return {"status": "already_running", "session_id": session_id}
        self.futures[session_id] = self.pool.submit(self.coordinator.record_turn, **turn)
        return {"status": "scheduled", "session_id": session_id}

    def result(self, session_id, timeout=None):
        future = self.futures.get(session_id)
        if future is None:
            return None
        return future.result(timeout=timeout)

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=False)
