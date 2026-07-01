import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from diagnostics.health import HealthCheck, module_available
from memory import init_store


class DiagnosticsHealthTests(unittest.TestCase):
    def test_optional_module_probe_is_safe(self):
        self.assertIsInstance(module_available("missing_parent.child"), bool)

    def test_initialized_data_root_is_detected(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            check = HealthCheck(data, state)
            result = check.run()
            data_row = next(row for row in result["checks"] if row["name"] == "data_root")
            self.assertEqual(data_row["status"], "ok")
            self.assertIsInstance(result["healthy"], bool)

    def test_uninitialized_data_root_is_blocking(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            result = HealthCheck(data, state).run()
            data_row = next(row for row in result["checks"] if row["name"] == "data_root")
            self.assertEqual(data_row["status"], "fail")
            self.assertFalse(result["healthy"])


if __name__ == "__main__":
    unittest.main()
