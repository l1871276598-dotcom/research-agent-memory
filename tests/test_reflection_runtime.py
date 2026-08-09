import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from laos import build_application
from memory import init_store


class ReflectionRuntimeTests(unittest.TestCase):
    def test_record_task_is_available(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            app = build_application(data, state)
            result = app.run({
                "type": "reflection.record",
                "workspace": "personal",
                "confidentiality": "personal",
                "input": {
                    "session_id": "session-1",
                    "messages": [{"role": "user", "content": "hello"}]
                }
            })
            self.assertEqual(result["agent_id"], "reflection_record_agent")
            self.assertEqual(result["output"]["status"], "not_due")
            self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
