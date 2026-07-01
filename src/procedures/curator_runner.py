from pathlib import Path

from .curator_plan import build_plan


class ProcedureCurator:
    def __init__(self, data_root, catalog):
        self.root = Path(data_root).expanduser().resolve(strict=False) / "procedures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog

    def report(self, now=None, stale_days=30, archive_days=90):
        names = []
        for document in self.root.glob("*/PROCEDURE.md"):
            if document.is_file() and not document.is_symlink() and not document.parent.is_symlink():
                names.append(document.parent.name)
        for name in sorted(names):
            self.catalog.ensure(name, now)
        report = build_plan(self.catalog.list(), now, stale_days, archive_days)
        report["procedures"] = sorted(names)
        return report
