import hashlib
import json


class ToolLoopDetector:
    def __init__(self, warn_after=2, stop_after=5):
        self.warn_after = warn_after
        self.stop_after = stop_after
        self.failures = {}

    def signature(self, name, arguments=None):
        value = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)
        return name, hashlib.sha256(value.encode("utf-8")).hexdigest()

    def record(self, name, arguments=None, failed=False):
        key = self.signature(name, arguments)
        if not failed:
            self.failures.pop(key, None)
            return {"action": "allow", "count": 0}
        count = self.failures.get(key, 0) + 1
        self.failures[key] = count
        if count >= self.stop_after:
            return {"action": "stop", "count": count}
        if count >= self.warn_after:
            return {"action": "warn", "count": count}
        return {"action": "allow", "count": count}

    def reset(self):
        self.failures.clear()
