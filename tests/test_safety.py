import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from safety.approval import ApprovalGate
from safety.loop import ToolLoopDetector


class SafetyTests(unittest.TestCase):
    def test_catastrophic_commands_are_denied(self):
        gate = ApprovalGate()
        self.assertEqual(gate.decide("rm -rf /").action, "deny")
        self.assertEqual(gate.decide("mkfs.ext4 /dev/sda1").action, "deny")
        self.assertEqual(gate.decide("echo hello").action, "allow")

    def test_reviewed_commands_need_session_scoped_approval(self):
        gate = ApprovalGate()
        token = gate.bind_session("session-one")
        try:
            command = "sudo apt update"
            self.assertEqual(gate.decide(command).action, "review")
            gate.approve(command, "once")
            self.assertEqual(gate.decide(command).action, "allow")
            self.assertEqual(gate.decide(command).action, "review")
            gate.approve(command, "session")
            self.assertEqual(gate.decide(command).action, "allow")
        finally:
            gate.reset_session(token)

    def test_identical_failures_warn_then_stop(self):
        detector = ToolLoopDetector(warn_after=2, stop_after=3)
        self.assertEqual(detector.record("search", {"q": "x"}, failed=True)["action"], "allow")
        self.assertEqual(detector.record("search", {"q": "x"}, failed=True)["action"], "warn")
        self.assertEqual(detector.record("search", {"q": "x"}, failed=True)["action"], "stop")
        self.assertEqual(detector.record("search", {"q": "x"}, failed=False)["action"], "allow")


if __name__ == "__main__":
    unittest.main()
