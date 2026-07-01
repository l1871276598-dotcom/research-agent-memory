class ModelBackendRegistry:
    def __init__(self):
        self._factories = {}

    def register(self, name, factory, *, allow_override=False):
        if not isinstance(name, str) or not name.strip() or not callable(factory):
            raise ValueError("invalid model backend registration")
        key = name.strip().lower()
        if key in self._factories and not allow_override:
            raise ValueError(f"model backend already registered: {key}")
        self._factories[key] = factory
        return factory

    def create(self, name, options=None):
        key = str(name).strip().lower()
        try:
            factory = self._factories[key]
        except KeyError as exc:
            raise ValueError(f"unknown model backend: {key}") from exc
        values = options or {}
        if not isinstance(values, dict):
            raise ValueError("model backend options must be an object")
        return factory(**values)

    def names(self):
        return sorted(self._factories)
