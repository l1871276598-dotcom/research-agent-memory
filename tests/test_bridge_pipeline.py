import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bridge import (
    BridgeEventInbox,
    BridgeIngestService,
    BridgePipeline,
    BridgeScopeResolver,
    SessionProjector,
    build_bridge_pipeline,
)
from bridge.recovery import recover_processing
from memory import init_store
from sessions import SessionStore


TOKEN = "bridge-test-token-123456789"


def event(
    event_id,
    message_id,
    role,
    content,
    *,
    parent=None,
    final=True,
    branch="main",
    version=1,
    account="account-hash",
    metadata=None,
):
    return {
        "event_id": event_id,
        "event_type": "message",
        "source": "chatgpt-web",
        "account_id": account,
        "conversation_id": "conversation-one",
        "branch_id": branch,
        "message_id": message_id,
        "parent_message_id": parent,
        "version": version,
        "role": role,
        "content": content,
        "is_final": final,
        "metadata": metadata or {},
    }


def checkpoint(event_id="checkpoint-one", branch="main"):
    return {
        "event_id": event_id,
        "event_type": "checkpoint",
        "source": "chatgpt-web",
        "account_id": "account-hash",
        "conversation_id": "conversation-one",
        "branch_id": branch,
        "metadata": {},
    }


class RecordingCoordinator:
    def __init__(self):
        self.calls = []

    def record_turn(self, **values):
        self.calls.append(values)
        return {
            "status": "reviewed" if values.get("force") else "not_due",
            "session_id": values["session_id"],
        }


class ReviewBackend:
    backend_id = "test"

    def __init__(self):
        self.calls = []

    def review(self, messages):
        self.calls.append(messages)
        return {
            "memory_candidates": [
                {
                    "type": "profile",
                    "title": "Code preference",
                    "scope": "global",
                    "content": "The user prefers minimal code changes.",
                    "tags": ["coding"],
                }
            ],
            "skill_candidates": [],
            "nothing_to_save": False,
        }


class BridgeInboxTests(unittest.TestCase):
    def test_streaming_updates_finalize_once_and_final_content_is_immutable(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            partial = event("event-one", "message-one", "assistant", "Hel", final=False)
            created = inbox.ingest(partial)
            self.assertEqual(created["status"], "captured")

            partial["content"] = "Hello"
            updated = inbox.ingest(partial)
            self.assertEqual(updated["ingest_status"], "updated")
            self.assertEqual(updated["status"], "captured")

            partial["is_final"] = True
            finalized = inbox.ingest(partial)
            self.assertEqual(finalized["status"], "queued")
            self.assertEqual(inbox.ingest(partial)["ingest_status"], "duplicate")

            changed = dict(partial, content="Changed final answer")
            with self.assertRaisesRegex(ValueError, "immutable"):
                inbox.ingest(changed)

            duplicate_identity = dict(partial, event_id="different-event")
            with self.assertRaisesRegex(ValueError, "identity already exists"):
                inbox.ingest(duplicate_identity)

    def test_parent_is_projected_before_child_even_when_child_arrives_first(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            inbox.ingest(
                event(
                    "assistant-event",
                    "assistant-message",
                    "assistant",
                    "answer",
                    parent="user-message",
                )
            )
            inbox.ingest(event("user-event", "user-message", "user", "question"))

            first = inbox.claim_next()
            self.assertEqual(first["event_id"], "user-event")
            inbox.mark_processed(first["event_id"])
            second = inbox.claim_next()
            self.assertEqual(second["event_id"], "assistant-event")

    def test_ingestion_requires_token_source_allowlist_and_bounded_json(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            service = BridgeIngestService(
                inbox,
                token=TOKEN,
                allowed_sources={"chatgpt-web"},
                max_payload_bytes=5000,
            )
            payload = json.dumps(event("one", "one", "user", "hello"))
            with self.assertRaises(PermissionError):
                service.ingest_json(payload, token="wrong-token-123456")
            accepted = service.ingest_json(payload, token=TOKEN)
            self.assertEqual(accepted["queue_status"], "queued")

            other = event("two", "two", "user", "hello")
            other["source"] = "unknown"
            with self.assertRaises(PermissionError):
                service.ingest_json(json.dumps(other), token=TOKEN)
            with self.assertRaises(ValueError):
                service.ingest_json("[]", token=TOKEN)


class SessionProjectorTests(unittest.TestCase):
    def test_messages_branch_scope_and_review_checkpoint_are_projected(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            sessions = SessionStore(state)
            coordinator = RecordingCoordinator()
            resolver = BridgeScopeResolver(
                {
                    ("chatgpt-web", "account-hash"): {
                        "workspace": "work",
                        "project": "laos",
                        "confidentiality": "internal",
                        "profile": "work-profile",
                    }
                }
            )
            projector = SessionProjector(
                inbox,
                sessions,
                coordinator=coordinator,
                scope_resolver=resolver,
            )

            inbox.ingest(event("user", "u1", "user", "Use minimal code."))
            inbox.ingest(event("assistant", "a1", "assistant", "Understood.", parent="u1"))
            projected = projector.drain()
            self.assertEqual([item["status"] for item in projected], ["processed", "processed"])
            session_id = projected[-1]["session_id"]
            session = sessions.get(session_id)
            self.assertEqual(session["workspace"], "work")
            self.assertEqual(session["project"], "laos")
            self.assertEqual([item["role"] for item in session["messages"]], ["user", "assistant"])
            self.assertTrue(session["messages"][1]["metadata"]["bridge_generated"])
            self.assertEqual(len(coordinator.calls), 1)
            self.assertEqual(coordinator.calls[0]["turn_increment"], 1)

            inbox.ingest(checkpoint())
            projector.drain()
            self.assertEqual(len(coordinator.calls), 2)
            self.assertTrue(coordinator.calls[-1]["force"])
            self.assertEqual(coordinator.calls[-1]["turn_increment"], 0)

            inbox.ingest(event("branch-user", "u2", "user", "Alternative", branch="branch-2"))
            branch_result = projector.drain()[0]
            self.assertNotEqual(branch_result["session_id"], session_id)

    def test_crash_recovery_does_not_duplicate_message_or_review(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            sessions = SessionStore(state)
            coordinator = RecordingCoordinator()
            projector = SessionProjector(inbox, sessions, coordinator=coordinator)
            inbox.ingest(event("assistant", "a1", "assistant", "answer"))

            claimed = inbox.claim_next()
            first = projector.project(claimed)
            self.assertEqual(first["event_id"], "assistant")
            self.assertEqual(len(coordinator.calls), 1)
            self.assertEqual(recover_processing(inbox), 1)

            replayed = projector.drain()
            self.assertEqual(replayed[0]["status"], "processed")
            session = sessions.get(replayed[0]["session_id"])
            self.assertEqual(len(session["messages"]), 1)
            self.assertEqual(len(coordinator.calls), 1)

    def test_restricted_scope_saves_locally_but_review_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as state:
            inbox = BridgeEventInbox(state)
            sessions = SessionStore(state)
            coordinator = RecordingCoordinator()
            projector = SessionProjector(
                inbox,
                sessions,
                coordinator=coordinator,
                scope_resolver=BridgeScopeResolver(
                    {"account-hash": {"workspace": "personal", "confidentiality": "restricted"}}
                ),
            )
            inbox.ingest(event("user", "u1", "user", "private"))
            inbox.ingest(event("assistant", "a1", "assistant", "reply", parent="u1"))
            result = projector.drain()[-1]
            self.assertEqual(result["status"], "processed")
            self.assertEqual(coordinator.calls[0]["confidentiality"], "restricted")
            self.assertEqual(len(sessions.get(result["session_id"])["messages"]), 2)


class BridgeEndToEndTests(unittest.TestCase):
    def test_capture_works_without_any_model_backend(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            pipeline = build_bridge_pipeline(data, state, token=TOKEN)
            user = pipeline.ingest(json.dumps(event("u", "u", "user", "hello")), token=TOKEN)
            assistant = pipeline.ingest(
                json.dumps(event("a", "a", "assistant", "reply", parent="u")),
                token=TOKEN,
            )
            self.assertEqual(user["projected"][0]["status"], "processed")
            self.assertIsNone(assistant["projected"][0]["review"])
            self.assertEqual(len(pipeline.sessions.list()), 1)

    def test_final_assistant_turn_triggers_candidate_only_review(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            init_store(data)
            backend = ReviewBackend()
            pipeline = build_bridge_pipeline(
                data,
                state,
                token=TOKEN,
                model_backend=backend,
                memory_interval=1,
                skill_interval=10,
            )
            pipeline.ingest(json.dumps(event("u", "u", "user", "I prefer minimal code.")), token=TOKEN)
            result = pipeline.ingest(
                json.dumps(event("a", "a", "assistant", "I will remember that.", parent="u")),
                token=TOKEN,
            )

            review = result["projected"][0]["review"]
            self.assertEqual(review["status"], "reviewed")
            self.assertEqual(len(review["result"]["candidate_ids"]), 1)
            self.assertEqual(len(backend.calls), 1)
            prompt = backend.calls[0][-1]["content"]
            self.assertIn("assistant-only assertion", prompt)


if __name__ == "__main__":
    unittest.main()
