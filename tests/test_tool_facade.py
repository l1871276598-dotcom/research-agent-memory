import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tool_facade import LaosToolFacade


class Application:
    def run(self, task):
        return {"task": task}


class Sessions:
    def list(self, **kwargs):
        return [kwargs]

    def get(self, session_id):
        return {"id": session_id}

    def search(self, query, **kwargs):
        return [{"query": query, **kwargs}]


class Proposals:
    def list(self, status):
        return [{"status": status}]

    def review(self, proposal_id, action, reason):
        return {"proposal_id": proposal_id, "action": action, "reason": reason}


class Manager:
    def apply(self, proposal_id):
        return {"applied": proposal_id}

    def rollback(self, proposal_id):
        return {"rolled_back": proposal_id}


class ToolFacadeTests(unittest.TestCase):
    def test_routes_to_native_services(self):
        facade = LaosToolFacade(Application(), Sessions(), Proposals(), Manager())
        self.assertEqual(facade.run_task({"type": "memory.search"})["task"]["type"], "memory.search")
        self.assertEqual(facade.session_get("one")["id"], "one")
        self.assertEqual(facade.session_search("PDC")[0]["query"], "PDC")
        self.assertEqual(facade.procedure_list()[0]["status"], "candidate")
        self.assertEqual(facade.procedure_apply("p")["applied"], "p")
        self.assertEqual(facade.procedure_rollback("p")["rolled_back"], "p")

    def test_missing_service_fails_closed(self):
        facade = LaosToolFacade(Application())
        with self.assertRaises(RuntimeError):
            facade.session_get("one")
        with self.assertRaises(RuntimeError):
            facade.procedure_list()


if __name__ == "__main__":
    unittest.main()
