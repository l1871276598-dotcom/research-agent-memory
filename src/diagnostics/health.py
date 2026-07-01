from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError):
        return False


class HealthCheck:
    def __init__(self, data_root, state_dir, codex_home=None):
        self.data_root = Path(data_root).expanduser()
        self.state_dir = Path(state_dir).expanduser()
        self.codex_home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"

    @staticmethod
    def item(name, status, detail="", fix=""):
        return {"name": name, "status": status, "detail": detail, "fix": fix}

    @staticmethod
    def writable(path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".laos-check-", dir=path)
            os.close(descriptor)
            os.unlink(temporary)
            return True
        except OSError:
            return False

    @staticmethod
    def fts5():
        try:
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def database(path):
        if not path.exists():
            return "missing"
        try:
            with sqlite3.connect(path) as connection:
                return str(connection.execute("PRAGMA quick_check").fetchone()[0])
        except sqlite3.Error as exc:
            return str(exc)

    def codex(self):
        binary = shutil.which("codex")
        if binary is None:
            return self.item("codex_cli", "fail", "not found", "Install Codex CLI and sign in.")
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self.item("codex_cli", "fail", str(exc), "Repair the Codex CLI installation.")
        text = (result.stdout or result.stderr).strip()
        match = _VERSION.search(text)
        if result.returncode != 0 or match is None:
            return self.item("codex_cli", "fail", text, "Update the Codex CLI installation.")
        version = tuple(int(part) for part in match.groups())
        return self.item(
            "codex_cli",
            "ok" if version >= (0, 130, 0) else "warn",
            text,
            "Update Codex CLI." if version < (0, 130, 0) else "",
        )

    def run(self):
        checks = [
            self.item(
                "python",
                "ok" if sys.version_info >= (3, 10) else "fail",
                sys.version.split()[0],
                "Use Python 3.10 or newer.",
            ),
            self.item(
                "data_root",
                "ok" if (self.data_root / ".research-agent-root").is_file() else "fail",
                str(self.data_root),
                "Initialize the LAOS data root.",
            ),
            self.item(
                "state_dir",
                "ok" if self.writable(self.state_dir) else "fail",
                str(self.state_dir),
                "Choose a writable state directory.",
            ),
            self.item(
                "sqlite_fts5",
                "ok" if self.fts5() else "fail",
                sqlite3.sqlite_version,
                "Use a Python build with SQLite FTS5.",
            ),
            self.codex(),
            self.item(
                "codex_oauth",
                "ok" if (self.codex_home / "auth.json").is_file() else "warn",
                str(self.codex_home / "auth.json"),
                "Sign in with the Codex CLI.",
            ),
            self.item(
                "mcp_sdk",
                "ok" if module_available("mcp.server.fastmcp") else "optional",
                "available" if module_available("mcp.server.fastmcp") else "not installed",
                "Install the optional MCP SDK only when serving LAOS over MCP.",
            ),
        ]
        for filename in ("sessions.sqlite", "conversation_review.sqlite", "procedure_proposals.sqlite"):
            result = self.database(self.state_dir / filename)
            checks.append(
                self.item(
                    f"database:{filename}",
                    "ok" if result in {"ok", "missing"} else "fail",
                    result,
                    "Restore or rebuild the damaged database.",
                )
            )
        blocking = [check for check in checks if check["status"] == "fail"]
        return {"healthy": not blocking, "checks": checks, "blocking": blocking}
