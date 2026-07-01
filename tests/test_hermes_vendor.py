import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from runtime import hermes


class HermesVendorTests(unittest.TestCase):
    def test_manifest_preserves_upstream_and_excludes_rejected_features(self):
        manifest = json.loads(
            (REPO_ROOT / "config" / "hermes_vendor.json").read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["integration_policy"]["preserve_upstream_files"])
        self.assertFalse(manifest["integration_policy"]["modify_vendor_files_directly"])
        self.assertIn("cron", manifest["exclude"])
        self.assertIn("batch_runner.py", manifest["exclude"])
        self.assertIn("trajectory_compressor.py", manifest["exclude"])

    def test_bootstrap_fails_closed_when_snapshot_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(hermes, "_VENDOR_ROOT", Path(directory)):
                with self.assertRaises(RuntimeError):
                    hermes.activate_vendor()

    def test_bootstrap_adds_snapshot_to_import_path_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run_agent.py").write_text("class AIAgent: pass\n", encoding="utf-8")
            with mock.patch.object(hermes, "_VENDOR_ROOT", root):
                with mock.patch.object(sys, "path", list(sys.path)):
                    hermes.activate_vendor()
                    hermes.activate_vendor()
                    self.assertEqual(sys.path.count(str(root)), 1)


if __name__ == "__main__":
    unittest.main()
