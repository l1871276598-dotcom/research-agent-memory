import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

import memory


class LaosCliErrorCodeTests(unittest.TestCase):
    def run_task(self, root, state, task):
        return subprocess.run(
            [
                sys.executable,
                str(SOURCE_DIR / "laos.py"),
                "--root",
                str(root),
                "--state-dir",
                str(state),
                "--task-json",
                json.dumps(task, ensure_ascii=False, separators=(",", ":")),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_safe_error(self, result, code, *sensitive_values):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["code"], code)
        self.assertIsInstance(payload["error"].get("message"), str)
        self.assertIsInstance(payload["error"].get("stage"), str)
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertNotIn("Traceback", result.stderr)
        for value in sensitive_values:
            self.assertNotIn(str(value), result.stderr)

    def test_invalid_memory_root_has_a_stable_safe_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "missing-private-memory-root"
            state = base / "state"
            state.mkdir()
            result = self.run_task(
                root,
                state,
                {
                    "type": "memory.search",
                    "input": {"query": "migration", "workspace": "personal"},
                },
            )

        self.assert_safe_error(result, "invalid_memory_root", root, state)

    def test_invalid_memory_file_has_a_stable_safe_validation_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "data"
            state = base / "state"
            memory.init_store(root)
            private_file = root / "memory" / "principles" / "private-patient-file.md"
            private_file.write_text("private-patient-content", encoding="utf-8")
            result = self.run_task(
                root,
                state,
                {
                    "type": "memory.search",
                    "input": {"query": "migration", "workspace": "personal"},
                },
            )

        self.assert_safe_error(
            result,
            "memory_store_validation_failed",
            root,
            state,
            private_file,
            "private-patient-content",
        )


if __name__ == "__main__":
    unittest.main()
