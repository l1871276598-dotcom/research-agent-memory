import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from diagnostics.health import HealthCheck, module_available
from memory import init_store


class DiagnosticsHealthTests(unittest.TestCase):
    def test_check_health_cli_starts_without_pythonpath(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_health.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_optional_module_probe_is_safe(self):
        self.assertIsInstance(module_available("missing_parent.child"), bool)

    def test_fts5_probe_closes_database_connection(self):
        connection = mock.MagicMock()
        with mock.patch("diagnostics.health.sqlite3.connect", return_value=connection):
            self.assertTrue(HealthCheck.fts5())
        connection.close.assert_called_once_with()

    def test_database_probe_closes_database_connection(self):
        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.return_value = ("ok",)
        with tempfile.TemporaryDirectory() as state:
            path = Path(state) / "probe.sqlite"
            path.touch()
            with mock.patch("diagnostics.health.sqlite3.connect", return_value=connection):
                self.assertEqual(HealthCheck.database(path), "ok")
        connection.close.assert_called_once_with()

    def test_initialized_data_root_is_detected(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            check = HealthCheck(data, state)
            result = check.run()
            data_row = next(row for row in result["checks"] if row["name"] == "data_root")
            self.assertEqual(data_row["status"], "ok")
            self.assertIsInstance(result["healthy"], bool)

    @mock.patch("diagnostics.health.shutil.which", return_value=None)
    def test_non_codex_backend_does_not_require_codex(self, which):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            result = HealthCheck(
                data,
                state,
                model_backend="openai_compatible",
            ).run()
            codex = next(row for row in result["checks"] if row["name"] == "codex_cli")
            oauth = next(row for row in result["checks"] if row["name"] == "codex_oauth")
            backend = next(row for row in result["checks"] if row["name"] == "model_backend")
            self.assertEqual(codex["status"], "optional")
            self.assertEqual(oauth["status"], "optional")
            self.assertEqual(backend["detail"], "openai_compatible")
            self.assertTrue(result["healthy"])

    def test_uninitialized_data_root_is_blocking(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            result = HealthCheck(data, state).run()
            data_row = next(row for row in result["checks"] if row["name"] == "data_root")
            self.assertEqual(data_row["status"], "fail")
            self.assertFalse(result["healthy"])


if __name__ == "__main__":
    unittest.main()
