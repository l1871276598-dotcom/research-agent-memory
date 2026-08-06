import argparse
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
DISTILL = SOURCE_DIR / "memory_distill.py"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))


class ActivationCrashAndConcurrencyTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(
            argparse.Namespace(
                root=str(self.root),
                state_dir=str(self.state),
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def create_candidate(self, *, title="Crash candidate", content="crash evidence"):
        from memory.candidate import CandidateStore

        return CandidateStore(self.root, self.state).create(
            {
                "type": "principle",
                "title": title,
                "scope": "global",
                "workspace": "personal",
                "confidentiality": "personal",
                "source": "manual:user_confirmed",
                "confidence": "confirmed",
                "content": content,
                "tags": ["activation"],
            }
        )

    def test_crash_after_backend_mutation_recovers_to_one_receipt(self):
        from memory.store import MemoryStore
        from review import AuthorityError, AuthorityMemoryStore
        from review.gate import ReviewGate

        created = self.create_candidate()
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])

        def mutate_then_crash(decision_record):
            gate.backend._accept_candidate_impl(
                argparse.Namespace(
                    root=str(self.root),
                    state_dir=str(self.state),
                    id=decision_record["candidate_snapshot"]["candidate_id"],
                ),
                authority=gate._authority,
            )
            raise RuntimeError("injected crash after backend mutation")

        with self.assertRaisesRegex(RuntimeError, "injected crash"):
            gate.authority_store.activate(
                decision["decision_id"],
                decision["expected_active_generation"],
                mutate_then_crash,
            )

        self.assertEqual(gate.authority_store.pending_count(), 1)
        self.assertEqual(
            MemoryStore(self.root).get(created["candidate_id"])["status"],
            "active",
        )
        guarded = AuthorityMemoryStore(
            MemoryStore(self.root), gate.authority_store
        )
        with self.assertRaises(AuthorityError) as blocked:
            guarded.active_relevant("crash evidence", "personal")
        self.assertEqual(blocked.exception.code, "activation_pending")

        recovered = gate.activate(
            decision["decision_id"],
            decision["expected_active_generation"],
        )

        self.assertEqual(recovered["status"], "committed")
        self.assertEqual(recovered["backend_status"], "recovered")
        self.assertEqual(recovered["active_generation"], 1)
        self.assertEqual(gate.authority_store.pending_count(), 0)
        rows = guarded.active_relevant("crash evidence", "personal")
        self.assertEqual([row["id"] for row in rows], [created["candidate_id"]])
        self.assertEqual(
            rows[0]["authority_receipt_id"], recovered["activation_id"]
        )

    def test_two_processes_converge_on_same_activation_receipt(self):
        from memory.store import MemoryStore
        from review.gate import ReviewGate

        created = self.create_candidate(
            title="Concurrent candidate",
            content="concurrent activation evidence",
        )
        gate = ReviewGate(self.root, self.state)
        decision = gate.review("accept", created["candidate_id"])
        command = [
            sys.executable,
            str(DISTILL),
            "activate",
            "--root",
            str(self.root),
            "--state-dir",
            str(self.state),
            "--decision-id",
            decision["decision_id"],
            "--expected-active-generation",
            str(decision["expected_active_generation"]),
        ]

        first = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_stdout, first_stderr = first.communicate(timeout=30)
        second_stdout, second_stderr = second.communicate(timeout=30)

        self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        first_id = re.search(r"Activation: (mact_[0-9a-f]{64})", first_stdout)
        second_id = re.search(r"Activation: (mact_[0-9a-f]{64})", second_stdout)
        self.assertIsNotNone(first_id, first_stdout)
        self.assertIsNotNone(second_id, second_stdout)
        self.assertEqual(first_id.group(1), second_id.group(1))
        self.assertEqual(gate.authority_store.current_generation(), 1)
        self.assertEqual(
            MemoryStore(self.root).get(created["candidate_id"])["status"],
            "active",
        )

        journal = self.root / "state/distill_runs.jsonl"
        entries = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
        ]
        completed = [
            entry
            for entry in entries
            if entry.get("status") == "completed"
            and created["candidate_id"] in entry.get("proposal_ids", [])
        ]
        self.assertEqual(len(completed), 1)


if __name__ == "__main__":
    unittest.main()
