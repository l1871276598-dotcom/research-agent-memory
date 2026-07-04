from pathlib import Path


class ProcedureCuratorTransitions:
    def __init__(self, data_root, catalog):
        self.root = Path(data_root).expanduser().resolve(strict=False) / "procedures"
        self.archive = self.root / ".archive"
        self.archive.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog

    def apply(self, change):
        if not isinstance(change, dict):
            raise ValueError("curator change must be an object")
        name = change.get("name")
        target = change.get("to")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid procedure name")
        if target not in {"active", "stale", "archived"}:
            raise ValueError("invalid procedure target state")

        state = self.catalog._load()
        record = state["records"].get(name)
        if not isinstance(record, dict):
            raise ValueError("procedure is not catalogued")
        if record.get("pinned") and target != "active":
            raise ValueError("pinned procedure cannot be transitioned")
        if record.get("state", "active") != change.get("from"):
            raise ValueError("procedure state changed after curator report")

        live = self.root / name
        archived = self.archive / name
        if live.is_symlink() or archived.is_symlink():
            raise ValueError("procedure path is redirected")
        if target == "archived":
            if not live.is_dir() or archived.exists():
                raise ValueError("procedure cannot be archived")
            live.rename(archived)
        elif target == "active" and record.get("state") == "archived":
            if not archived.is_dir() or live.exists():
                raise ValueError("procedure cannot be restored")
            archived.rename(live)

        record["state"] = target
        self.catalog._save(state)
        return {"name": name, "state": target}
