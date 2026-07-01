import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from agents.reflection import ConversationReviewAgent
from reflection.conversation_review import (
    ConversationReviewService,
    ReviewCounter,
    digest_history,
    parse_review_response,
    prepare_review_messages,
)


class FakeMemoryCore:
    def __init__(self):
        self.values = []

    def create_candidate(self, values):
        self.values.append(dict(values))
        return {"candidate_id": f"candidate-{len(self.values)}", "status": "candidate"}


class ConversationReviewTests(unittest.TestCase):
    def test_routed_digest_keeps_tool_result_with_assistant_call(self):
        messages = [
            {"role": "user", "content": "old question"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "search", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
            {"role": "user", "content": "recent question"},
            {"role": "assistant", "content": "recent answer"},
        ]

        digested = digest_history(messages, tail=3)

        self.assertEqual(digested[0]["role"], "user")
        self.assertIn("old question", digested[0]["content"])
        self.assertEqual(digested[1]["role"], "assistant")
        self.assertEqual(digested[2]["role"], "tool")

    def test_prepare_supports_skill_only_review_with_strict_schema(self):
        prepared = prepare_review_messages(
            [{"role": "user", "content": "Use fewer abstractions next time."}],
            review_memory=False,
            review_skills=True,
        )

        prompt = prepared[-1]["content"]
        self.assertIn("memory_candidates must be empty", prompt)
        self.assertIn("skill_candidates", prompt)
        self.assertIn("nothing_to_save", prompt)

    def test_response_parser_requires_exact_consistent_schema(self):
        valid = {
            "memory_candidates": [],
            "skill_candidates": [],
            "nothing_to_save": True,
        }
        self.assertEqual(parse_review_response(json.dumps(valid)), valid)

        invalid = [
            {},
            {**valid, "extra": None},
            {**valid, "nothing_to_save": False},
            {
                "memory_candidates": [{"title": "x"}],
                "skill_candidates": [],
                "nothing_to_save": True,
            },
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_review_response(value)

    def test_counter_matches_memory_turn_and_skill_iteration_thresholds(self):
        counter = ReviewCounter(memory_interval=2, skill_interval=3)

        self.assertEqual(
            counter.record_turn(tool_iterations=1),
            {"review_memory": False, "review_skills": False},
        )
        self.assertEqual(
            counter.record_turn(tool_iterations=2),
            {"review_memory": True, "review_skills": True},
        )
        self.assertEqual(counter.turns_since_memory, 0)
        self.assertEqual(counter.iterations_since_skill, 0)

    def test_apply_forces_laos_ownership_and_creates_candidates_only(self):
        core = FakeMemoryCore()
        service = ConversationReviewService(core)
        response = {
            "memory_candidates": [
                {
                    "type": "profile",
                    "title": "Code preference",
                    "scope": "global",
                    "workspace": "work",
                    "confidentiality": "public",
                    "source": "untrusted:model",
                    "content": "The user prefers minimal code.",
                    "action": "update",
                    "confidence": "confirmed",
                    "status": "active",
                    "target_id": "mem-existing",
                    "project": "wrong-project",
                    "tags": ["coding"],
                }
            ],
            "skill_candidates": [{"name": "coding", "change": "Prefer minimal code."}],
            "nothing_to_save": False,
        }

        result = service.apply(
            response,
            workspace="personal",
            confidentiality="personal",
            project="laos",
            session_id="session-1",
        )

        self.assertEqual(result["candidate_ids"], ["candidate-1"])
        self.assertEqual(len(result["skill_candidates"]), 1)
        stored = core.values[0]
        self.assertEqual(stored["workspace"], "personal")
        self.assertEqual(stored["confidentiality"], "personal")
        self.assertEqual(stored["source"], "agent:conversation_review")
        self.assertEqual(stored["confidence"], "inferred")
        self.assertEqual(stored["action"], "create")
        self.assertEqual(stored["status"], "candidate")
        self.assertEqual(stored["project"], "laos")
        self.assertNotIn("target_id", stored)
        self.assertEqual(stored["source_refs"], ["session:session-1"])

    def test_reviewer_is_injected_and_agent_does_not_activate_memory(self):
        core = FakeMemoryCore()
        captured = []

        def reviewer(messages):
            captured.extend(messages)
            return {
                "memory_candidates": [
                    {
                        "type": "principle",
                        "title": "Review before activation",
                        "scope": "global",
                        "content": "Automatic extraction must create candidates only.",
                    }
                ],
                "skill_candidates": [],
                "nothing_to_save": False,
            }

        service = ConversationReviewService(core, reviewer)
        result = service.review(
            [{"role": "user", "content": "Remember to review changes first."}],
            workspace="personal",
        )

        self.assertEqual(result["candidate_ids"], ["candidate-1"])
        self.assertEqual(core.values[0]["status"], "candidate")
        self.assertIn("Return JSON only", captured[-1]["content"])

        agent = ConversationReviewAgent(service)
        prepared = agent.run(
            {
                "type": "reflection.prepare",
                "workspace": "personal",
                "input": {"messages": [{"role": "user", "content": "hello"}]},
            },
            {},
        )
        self.assertEqual(prepared["agent_id"], "conversation_review_agent")
        self.assertEqual(prepared["candidates"], [])


if __name__ == "__main__":
    unittest.main()
