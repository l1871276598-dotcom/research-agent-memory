"""LAOS setup diagnostics selectively adapted from Hermes doctor checks."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_MIN_CODEX = (0, 130, 0)


class Doctor:
    def __init__(self, data_root, state_dir, *, codex_home=None):
        self.data_root = Path(data_root).expanduser()
        self.state_dir = Path(state_dir).expanduser()
        self.codex_home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"

    @staticmethod
    def _item(name, status, detail="", fix=""):
        return {"name": name, "status": status, "detail": detail, "fix": fix}

    @staticmethod
    def _writable(path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".laos-doctor-", dir=path)
            os.close(descriptor)
            os.unlink(temporary)
            return True
        except OSError:
            return False

    @staticmethod
    def _sqlite_fts5():
        try:
            connection = sqlite3.connect(":memory:")
            connection.execute("CREATE VIRTUAL TABLE test_fts USING fts5(content)")
            connection.close()
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _database(path):
        if not path.exists():
            return "missing"
        try:
            with sqlite3.connect(path) as connection:
                value = connection.execute("PRAGMA quick_check").fetchone()[0]
            return "ok" if value == "ok" else str(value)
        except sqlite3.Error as exc:
            return str(exc)

    def _codex(self):
        binary = shutil.which("codex")
        if binary is None:
            return self._item(
                "codex_cli",
                "fail",
                "codex executable not found",
                "Install with `npm i -g @openai/codex`, then run `codex login`.",
            )
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._item("codex_cli", "fail", str(exc), "Reinstall Codex CLI.")
        text = (result.stdout or result.stderr).strip()
        match = _VERSION.search(text)
        if result.returncode != 0 or match is None:
            return self._item("codex_cli", "fail", text, "Reinstall or update Codex CLI.")
        version = tuple(int(value) for value in match.groups())
        status = "ok" if version >= _MIN_CODEX else "warn"
        fix = "Update with `npm i -g @openai/codex`." if status == "warn" else ""
        return self._item("codex_cli", status, text, fix)

    def run(self):
        checks = []
        python_ok = sys.version_info >= (3, 10)
        checks.append(
            self._item(
                "python",
                "ok" if python_ok else "fail",
                sys.version.split()[0],
                "Install Python 3.10 or newer." if not python_ok else "",
            )
        )
        checks.append(
            self._item(
                "data_root",
                "ok" if self.data_root.is_dir() else "fail",
                str(self.data_root),
                "Initialize the LAOS data root." if not self.data_root.is_dir() else "",
            )
        )
        checks.append(
            self._item(
                "state_dir_writable",
                "ok" if self._writable(self.state_dir) else "fail",
                str(self.state_dir),
                "Choose a writable local state directory.",
            )
        )
        checks.append(
            self._item(
                "sqlite_fts5",
                "ok" if self._sqlite_fts5() else "fail",
                sqlite3.sqlite_version,
                "Install a Python build with SQLite FTS5 support.",
            )
        )
        checks.append(self._codex())
        auth_path = self.codex_home / "auth.json"
        checks.append(
            self._item(
                "codex_oauth",
                "ok" if auth_path.is_file() else "warn",
                str(auth_path),
                "Run `codex login` to use ChatGPT subscription authentication.",
            )
        )
        checks.append(
            self._item(
                "mcp_sdk",
                "ok" if importlib.util.find_spec("mcp.server.fastmcp") else "optional",
                "FastMCP available" if importlib.util.find_spec("mcp.server.fastmcp") else "not installed",
                "Install the optional MCP SDK only when serving LAOS over MCP.",
            )
        )
        for filename in (
            "sessions.sqlite",
            "conversation_review.sqlite",
            "procedure_proposals.sqlite",
        ):
            value = self._database(self.state_dir / filename)
            checks.append(
                self._item(
                    f"database:{filename}",
                    "ok" if value in {"ok", "missing"} else "fail",
                    value,
                    "Restore the database from backup or rebuild its index." if value not in {"ok", "missing"} else "",
                )
            )
        blocking = [item for item in checks if item["status"] == "fail"]
        return {
            "healthy": not blocking,
            "checks": checks,
            "blocking": blocking,
        }
