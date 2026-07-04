import json
from datetime import datetime, timezone
from pathlib import Path


def _stamp(now=None):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(microsecond=0).isoformat()


class ProcedureCatalog:
    def __init__(self, state_dir):
        self.path = Path(state_dir).expanduser() / "procedure_catalog.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({"records": {}})

    def _load(self):
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("records"), dict):
            raise ValueError("invalid procedure catalog")
        return value

    def _save(self, value):
        self.path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def ensure(self, name, now=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid procedure name")
        value = self._load()
        records = value["records"]
        if name not in records:
            stamp = _stamp(now)
            records[name] = {
                "state": "active",
                "pinned": False,
                "use_count": 0,
                "view_count": 0,
                "created_at": stamp,
                "last_activity_at": None,
            }
            self._save(value)
        return {"name": name, **records[name]}

    def list(self):
        value = self._load()
        return [{"name": name, **record} for name, record in sorted(value["records"].items())]
