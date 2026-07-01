import argparse
import hashlib
import json
import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
import traceback
import unittest
import uuid
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"

if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from agents.base import BaseAgent, validate_result
from agents.registry import AgentRegistry
from orchestrator import Orchestrator


class EchoAgent(BaseAgent):
    agent_id = "echo_agent"
    handles = ["echo.run"]

    def run(self, task, context):
        return self.result(task, {"context": context})


class ImportAgent(BaseAgent):
    agent_id = "import_agent"
    handles = ["import.file", "import.chatgpt"]


class MemoryAgent(BaseAgent):
    agent_id = "memory_agent"
    handles = ["memory.create"]


class SearchAgent(BaseAgent):
    agent_id = "search_agent"
    handles = ["memory.search"]


class ReviewAgent(BaseAgent):
    agent_id = "review_agent"
    handles = ["memory.review"]


class ContextAgent(BaseAgent):
    agent_id = "context_agent"
    handles = ["context.build"]


class RecordingAgent(BaseAgent):
    def __init__(self, agent_id, handles, events, output=None, failure=None):
        self.agent_id = agent_id
        self.handles = handles
        self.events = events
        self.output = output or {}
        self.failure = failure
        self.calls = []

    def run(self, task, context):
        self.events.append(("run", self.agent_id))
        self.calls.append((task, context))
        if self.failure is not None:
            raise self.failure
        return self.result(task, self.output)


class RecordingRegistry(AgentRegistry):
    def __init__(self, agents, events):
        super().__init__(agents)
        self.events = events

    def select(self, task):
        self.events.append(("select", task["type"]))
        return super().select(task)


class BaseAgentRegistryTests(unittest.TestCase):
    def test_base_agent_contract_and_exact_handling(self):
        agent = EchoAgent()

        self.assertTrue(agent.can_handle({"type": "echo.run"}))
        self.assertFalse(agent.can_handle({"type": "echo"}))
        self.assertFalse(agent.can_handle({"type": " echo.run"}))
        self.assertFalse(agent.can_handle({}))
        with self.assertRaises(NotImplementedError):
            BaseAgent().run({"type": "unused"}, {})

    def test_result_is_deterministic_and_has_exact_schema(self):
        first_task = {"type": "echo.run", "input": {"value": 1, "label": "x"}}
        reordered_task = {"input": {"label": "x", "value": 1}, "type": "echo.run"}

        first = EchoAgent().run(first_task, {})
        second = EchoAgent().run(reordered_task, {})

        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {
                "agent_id",
                "task_type",
                "output",
                "candidates",
                "requires_review",
                "metadata",
            },
        )
        self.assertIs(first["requires_review"], True)
        self.assertEqual(set(first["metadata"]), {"trace_id", "confidence"})
        self.assertEqual(str(uuid.UUID(first["metadata"]["trace_id"])), first["metadata"]["trace_id"])

    def test_result_validates_supplied_trace_id_and_arguments(self):
        supplied = "A0B1C2D3-E4F5-4678-9012-ABCDEFABCDEF"
        result = EchoAgent().result(
            {"type": "echo.run", "trace_id": supplied},
            {},
            candidates=["candidate-1"],
            confidence=0.25,
        )
        self.assertEqual(result["metadata"]["trace_id"], str(uuid.UUID(supplied)))
        self.assertEqual(result["metadata"]["confidence"], 0.25)

        invalid_calls = [
            (None, {}, [], 1.0),
            ({}, {}, [], 1.0),
            ({"type": " "}, {}, [], 1.0),
            ({"type": "echo.run", "trace_id": "not-a-uuid"}, {}, [], 1.0),
            ({"type": "echo.run"}, [], [], 1.0),
            ({"type": "echo.run"}, {}, "candidate-1", 1.0),
            ({"type": "echo.run"}, {}, [], True),
            ({"type": "echo.run"}, {}, [], -0.1),
            ({"type": "echo.run"}, {}, [], 1.1),
        ]
        for task, output, candidates, confidence in invalid_calls:
            with self.subTest(task=task, output=output, candidates=candidates, confidence=confidence):
                with self.assertRaises(ValueError):
                    EchoAgent().result(task, output, candidates, confidence)

    def test_result_rejects_invalid_declared_agent_id(self):
        for agent_id in (None, "", "   "):
            agent = type("InvalidIdAgent", (BaseAgent,), {"agent_id": agent_id})()
            with self.subTest(agent_id=agent_id):
                with self.assertRaises(ValueError):
                    agent.result({"type": "echo.run"}, {})

    def test_validate_result_accepts_valid_result(self):
        agent = EchoAgent()
        task = {"type": "echo.run"}
        result = agent.result(task, {}, ["candidate-1"], 0.5)

        self.assertIs(validate_result(result, agent, task), result)

    def test_validate_result_rejects_every_schema_violation_safely(self):
        agent = EchoAgent()
        task = {"type": "echo.run"}
        valid = agent.result(task, {}, ["candidate-1"], 0.5)
        invalid_results = [
            None,
            {**valid, "extra": None},
            {key: value for key, value in valid.items() if key != "output"},
            {**valid, "agent_id": "other"},
            {**valid, "task_type": "echo.other"},
            {**valid, "output": []},
            {**valid, "candidates": "candidate-1"},
            {**valid, "candidates": [1]},
            {**valid, "requires_review": 1},
            {**valid, "requires_review": False},
            {**valid, "metadata": {**valid["metadata"], "extra": None}},
            {**valid, "metadata": {"trace_id": "bad", "confidence": 0.5}},
            {**valid, "metadata": {"trace_id": valid["metadata"]["trace_id"], "confidence": True}},
            {**valid, "metadata": {"trace_id": valid["metadata"]["trace_id"], "confidence": 2.0}},
        ]
        for invalid in invalid_results:
            with self.subTest(result=invalid):
                with self.assertRaises(ValueError):
                    validate_result(invalid, agent, task)

    def test_validate_result_rejects_invalid_declared_agent_id(self):
        task = {"type": "echo.run"}
        for agent_id in (None, "", "   "):
            agent = type("InvalidIdAgent", (BaseAgent,), {"agent_id": agent_id})()
            result = EchoAgent().result(task, {})
            result["agent_id"] = agent_id
            with self.subTest(agent_id=agent_id):
                with self.assertRaises(ValueError):
                    validate_result(result, agent, task)

    def test_registry_selects_only_exact_task_type(self):
        agent = EchoAgent()
        registry = AgentRegistry([agent])

        self.assertIs(registry.select({"type": "echo.run"}), agent)
        for task in (None, {}, {"type": ""}, {"type": " "}, {"type": "echo"}):
            with self.subTest(task=task):
                with self.assertRaises(ValueError):
                    registry.select(task)

    def test_registry_rejects_invalid_agents_ids_and_handles(self):
        cases = [
            [object()],
            [EchoAgent(), EchoAgent()],
            [type("BlankId", (BaseAgent,), {"agent_id": " ", "handles": ["one"]})()],
            [type("BlankHandle", (BaseAgent,), {"agent_id": "one", "handles": [" "]})()],
            [type("DuplicateHandle", (BaseAgent,), {"agent_id": "one", "handles": ["x", "x"]})()],
            [
                type("First", (BaseAgent,), {"agent_id": "one", "handles": ["same"]})(),
                type("Second", (BaseAgent,), {"agent_id": "two", "handles": ["same"]})(),
            ],
        ]
        for agents in cases:
            with self.subTest(agents=agents):
                with self.assertRaises(ValueError):
                    AgentRegistry(agents)

    def test_registry_loads_declared_five_agent_config(self):
        agents = [ImportAgent(), MemoryAgent(), SearchAgent(), ReviewAgent(), ContextAgent()]
        registry = AgentRegistry.from_config(SOURCE_DIR / "agents" / "registry.yaml", agents)

        self.assertIs(registry.select({"type": "import.chatgpt"}), agents[0])
        self.assertIs(registry.select({"type": "context.build"}), agents[-1])

    def test_registry_rejects_malformed_config_and_object_mismatches(self):
        agent = EchoAgent()
        malformed_configs = [
            [],
            {},
            {"agents": [], "extra": None},
            {"agents": {}},
            {"agents": [{"id": "echo_agent", "class": "EchoAgent"}]},
            {"agents": [{"id": "echo_agent", "class": "EchoAgent", "handles": ["echo.run"], "extra": 1}]},
            {"agents": [{"id": "echo_agent", "class": "UnknownAgent", "handles": ["echo.run"]}]},
            {"agents": [{"id": "other", "class": "EchoAgent", "handles": ["echo.run"]}]},
            {"agents": [{"id": "echo_agent", "class": "EchoAgent", "handles": ["echo.other"]}]},
            {
                "agents": [
                    {"id": "echo_agent", "class": "EchoAgent", "handles": ["echo.run"]},
                    {"id": "echo_agent", "class": "EchoAgent", "handles": ["echo.other"]},
                ]
            },
            {
                "agents": [
                    {"id": "echo_agent", "class": "EchoAgent", "handles": ["echo.run"]},
                    {"id": "other", "class": "EchoAgent", "handles": ["echo.run"]},
                ]
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.yaml"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                AgentRegistry.from_config(path, [agent])

            for config in malformed_configs:
                with self.subTest(config=config):
                    path.write_text(json.dumps(config), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        AgentRegistry.from_config(path, [agent])


class OrchestratorTests(unittest.TestCase):
    def make_kernel(self, context_output=None, target_output=None, target_failure=None):
        events = []
        context_agent = RecordingAgent(
            "context_agent",
            ["context.build"],
            events,
            context_output or {"entries": ["context"]},
        )
        target_agent = RecordingAgent(
            "search_agent",
            ["memory.search"],
            events,
            target_output or {"matches": ["memory"]},
            target_failure,
        )
        registry = RecordingRegistry([context_agent, target_agent], events)
        return Orchestrator(registry), registry, context_agent, target_agent, events

    def test_context_is_built_and_validated_before_target_selection_and_run(self):
        context_output = {"entries": ["prepared"]}
        kernel, registry, context_agent, target_agent, events = self.make_kernel(
            context_output=context_output
        )
        task = {
            "type": "memory.search",
            "input": {"query": "PDC"},
            "context_limit": 1200,
        }

        result = kernel.run(task)

        self.assertEqual(
            events,
            [
                ("select", "context.build"),
                ("run", "context_agent"),
                ("select", "memory.search"),
                ("run", "search_agent"),
            ],
        )
        context_task, context = context_agent.calls[0]
        self.assertIs(context_task["input"]["task"], task)
        self.assertEqual(context_task["context_limit"], 1200)
        self.assertEqual(context, {})
        self.assertIs(target_agent.calls[0][0], task)
        self.assertIs(target_agent.calls[0][1], context_output)
        self.assertEqual(result, target_agent.result(task, target_agent.output))
        self.assertIs(kernel.registry, registry)
        self.assertEqual(kernel.__dict__, {"registry": registry})
        self.assertFalse(hasattr(kernel, "memory"))
        self.assertFalse(hasattr(kernel, "review_gate"))

    def test_constructor_requires_agent_registry(self):
        with self.assertRaises(ValueError):
            Orchestrator(object())

    def test_default_context_limit_is_8000_and_result_envelope_is_unchanged(self):
        kernel, _, context_agent, target_agent, _ = self.make_kernel()
        task = {"type": "memory.search", "input": {}}
        expected = target_agent.result(task, target_agent.output)
        target_agent.run = lambda received_task, context: expected

        result = kernel.run(task)

        self.assertEqual(context_agent.calls[0][0]["context_limit"], 8000)
        self.assertIs(result, expected)

    def test_direct_context_build_selects_once_and_runs_with_empty_context(self):
        events = []
        context_agent = RecordingAgent(
            "context_agent", ["context.build"], events, {"entries": []}
        )
        registry = RecordingRegistry([context_agent], events)
        kernel = Orchestrator(registry)
        task = {"type": "context.build", "input": {"task": {"type": "memory.search"}}}

        result = kernel.run(task)

        self.assertEqual(events, [("select", "context.build"), ("run", "context_agent")])
        self.assertEqual(context_agent.calls, [(task, {})])
        self.assertEqual(result["agent_id"], "context_agent")

    def test_malformed_tasks_are_rejected_before_registry_use(self):
        malformed = [
            None,
            [],
            {},
            {"type": None},
            {"type": ""},
            {"type": "   "},
            {"type": "memory.search", "input": []},
            {"type": "memory.search", "input": None},
            {"type": "memory.search", "context_limit": True},
            {"type": "memory.search", "context_limit": 0},
            {"type": "memory.search", "context_limit": -1},
            {"type": "memory.search", "context_limit": 1.5},
            {"type": "memory.search", "context_limit": "8000"},
        ]
        for task in malformed:
            with self.subTest(task=task):
                kernel, _, _, _, events = self.make_kernel()
                with self.assertRaises(ValueError):
                    kernel.run(task)
                self.assertEqual(events, [])

    def test_invalid_context_envelope_is_rejected_before_target_selection(self):
        kernel, _, context_agent, target_agent, events = self.make_kernel()
        context_agent.run = lambda task, context: {"output": {}}

        with self.assertRaises(ValueError):
            kernel.run({"type": "memory.search"})

        self.assertEqual(events, [("select", "context.build")])
        self.assertEqual(target_agent.calls, [])

    def test_invalid_target_envelope_is_rejected(self):
        kernel, _, _, target_agent, _ = self.make_kernel()
        target_agent.run = lambda task, context: {"output": {}}

        with self.assertRaises(ValueError):
            kernel.run({"type": "memory.search"})

    def test_target_failure_propagates_unchanged(self):
        failure = RuntimeError("target failed")
        kernel, _, _, _, _ = self.make_kernel(target_failure=failure)

        with self.assertRaises(RuntimeError) as raised:
            kernel.run({"type": "memory.search"})

        self.assertIs(raised.exception, failure)

    def test_orchestrator_sources_are_router_only_re_exports_without_write_primitives(self):
        implementation = (SOURCE_DIR / "orchestrator" / "orchestrator.py").read_text(
            encoding="utf-8"
        )
        package = (SOURCE_DIR / "orchestrator" / "__init__.py").read_text(encoding="utf-8")
        compatibility = (SOURCE_DIR / "orchestrator.py").read_text(encoding="utf-8")

        forbidden = [
            "memory",
            "review",
            "context.builder",
            "pathlib",
            "sqlite",
            "subprocess",
            "open(",
            "write(",
            "write_text",
            "write_bytes",
            "candidate",
            "lifecycle",
        ]
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, implementation.lower())
        self.assertEqual(package.strip(), 'from .orchestrator import Orchestrator\n\n\n__all__ = ["Orchestrator"]')
        self.assertEqual(
            compatibility.strip(),
            'from orchestrator.orchestrator import Orchestrator\n\n\n__all__ = ["Orchestrator"]',
        )


def write_memory(root, **overrides):
    from memory import TYPE_DIRS, render_memory

    record = {
        "id": "mem-default",
        "type": "principle",
        "title": "Default",
        "created": "2026-06-29",
        "updated": "2026-06-29",
        "status": "active",
        "scope": "global",
        "workspace": "personal",
        "confidentiality": "personal",
        "source": "test",
        "confidence": "confirmed",
        "content": "default content",
        "tags": [],
    }
    record.update(overrides)
    path = root / TYPE_DIRS[record["type"]] / f"{record['id']}-test.md"
    path.write_text(render_memory(record), encoding="utf-8")
    return path


class MemoryLifecycleTests(unittest.TestCase):
    def test_facade_preserves_legacy_api_and_loads_legacy_module_once(self):
        import memory
        import memory_distill

        legacy_path = (SOURCE_DIR / "memory.py").resolve()
        loaded = [
            module
            for module in sys.modules.values()
            if getattr(module, "__file__", None)
            and Path(module.__file__).resolve() == legacy_path
        ]
        self.assertEqual(len(loaded), 1)
        legacy = loaded[0]
        self.assertIs(memory._require_data_root, legacy._require_data_root)
        self.assertIs(memory_distill._require_data_root, memory._require_data_root)
        self.assertTrue(callable(memory.init_store))

        importlib.reload(memory)

        loaded_again = [
            module
            for module in sys.modules.values()
            if getattr(module, "__file__", None)
            and Path(module.__file__).resolve() == legacy_path
        ]
        self.assertEqual(loaded_again, [legacy])

    def test_lifecycle_has_exact_states_and_transitions(self):
        from memory.lifecycle import STATES, TRANSITIONS, can_transition

        self.assertEqual(
            STATES,
            {"raw", "candidate", "active", "deprecated", "conflicted", "archived"},
        )
        self.assertEqual(
            TRANSITIONS,
            {
                "raw": {"candidate"},
                "candidate": {"active", "archived", "conflicted"},
                "active": {"deprecated", "conflicted", "archived"},
            },
        )
        self.assertTrue(can_transition("raw", "candidate"))
        self.assertFalse(can_transition("raw", "active"))

    def test_lifecycle_policy_is_immutable(self):
        from memory.lifecycle import STATES, TRANSITIONS, can_transition

        with self.assertRaises(AttributeError):
            STATES.add("review")
        with self.assertRaises(TypeError):
            TRANSITIONS["raw"] = {"active"}
        with self.assertRaises(AttributeError):
            TRANSITIONS["raw"].add("active")
        self.assertFalse(can_transition("raw", "active"))

    def test_lifecycle_validators_reject_invalid_values_and_transitions(self):
        from memory.lifecycle import can_transition, validate_state, validate_transition

        self.assertEqual(validate_state("active"), "active")
        self.assertTrue(validate_transition("candidate", "active"))
        for value in (None, True, False, "", "review", "conflict"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_state(value)
        for source, target in ((True, "candidate"), ("raw", False)):
            with self.subTest(source=source, target=target):
                with self.assertRaises(ValueError):
                    can_transition(source, target)
        self.assertFalse(can_transition("archived", "raw"))
        with self.assertRaises(ValueError):
            validate_transition("raw", "active")


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        from memory import init_store

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        init_store(self.root)
        write_memory(
            self.root,
            id="mem-alpha",
            title="PDC Alpha",
            content="PDC 血糖管理 evidence",
            tags=["PDC", "focus"],
        )
        write_memory(
            self.root,
            id="mem-beta",
            title="PDC Beta",
            content="PDC evidence",
        )
        write_memory(
            self.root,
            id="mem-candidate",
            status="candidate",
            title="PDC candidate",
            content="PDC candidate content",
        )
        write_memory(
            self.root,
            id="mem-deprecated",
            status="deprecated",
            title="PDC deprecated",
            content="PDC deprecated content",
        )
        write_memory(
            self.root,
            id="mem-restricted",
            workspace="work",
            confidentiality="restricted",
            title="PDC restricted",
            content="PDC restricted secret",
        )
        write_memory(
            self.root,
            id="mem-work",
            workspace="work",
            confidentiality="internal",
            title="PDC work",
            content="PDC internal evidence",
        )
        write_memory(
            self.root,
            id="mem-project-one",
            project="project-one",
            title="PDC project one",
            content="PDC project evidence",
        )
        write_memory(
            self.root,
            id="mem-project-two",
            project="project-two",
            title="PDC project two",
            content="PDC project evidence",
        )
        write_memory(
            self.root,
            id="mem-conflict",
            status="conflict",
            title="legacy conflict",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_records_are_sorted_copies_with_paths_and_conflict_normalization(self):
        from memory import collect_validated_records
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        records = store.records()

        self.assertEqual([row["id"] for row in records], sorted(row["id"] for row in records))
        self.assertTrue(all(row["relative_path"].startswith("memory/") for row in records))
        self.assertEqual(store.get("mem-conflict")["status"], "conflicted")
        records[0]["tags"].append("changed")
        self.assertNotIn("changed", store.get(records[0]["id"])["tags"])
        _, source_rows, errors, _ = collect_validated_records(self.root)
        self.assertEqual(errors, [])
        source = next(row["record"] for row in source_rows if row["record"]["id"] == "mem-conflict")
        self.assertEqual(source["status"], "conflict")

    def test_get_and_store_validation_fail_safely(self):
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        for memory_id in (None, True, "", "   ", "missing"):
            with self.subTest(memory_id=memory_id):
                with self.assertRaises(ValueError):
                    store.get(memory_id)
        with self.assertRaises(ValueError):
            MemoryStore(Path(self.temp.name) / "not-initialized")

        (self.root / "memory/principles/bad.md").write_text("not front matter", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "memory store validation failed"):
            store.records()

    def test_active_relevant_enforces_visibility_scope_and_deterministic_ranking(self):
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        personal = store.active_relevant("pdc 血糖管理", workspace="personal")
        self.assertEqual(personal[0]["id"], "mem-alpha")
        self.assertEqual(
            {row["id"] for row in personal},
            {"mem-alpha", "mem-beta"},
        )
        self.assertEqual(
            personal,
            store.active_relevant("pdc 血糖管理", workspace="personal"),
        )
        self.assertEqual(
            {row["id"] for row in store.active_relevant("PDC", workspace="work")},
            {"mem-work"},
        )
        with_project = store.active_relevant(
            "PDC", workspace="personal", project="project-one"
        )
        self.assertEqual(
            {row["id"] for row in with_project},
            {"mem-alpha", "mem-beta", "mem-project-one"},
        )
        self.assertNotIn("mem-project-two", {row["id"] for row in with_project})
        self.assertEqual(
            store.active_relevant("zero-match-term", workspace="personal"), []
        )
        self.assertNotIn(
            "mem-restricted",
            {
                row["id"]
                for row in store.active_relevant("restricted", workspace="work")
            },
        )

    def test_active_relevant_rejects_invalid_arguments_and_has_no_unsafe_switches(self):
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        for query in (None, True, "", "   "):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    store.active_relevant(query, workspace="personal")
        for kwargs in (
            {},
            {"workspace": None},
            {"workspace": True},
            {"workspace": ""},
            {"workspace": "shared"},
            {"workspace": "personal", "project": False},
            {"workspace": "personal", "project": "   "},
            {"workspace": "personal", "include_restricted": True},
            {"workspace": "personal", "include_inactive": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    store.active_relevant("PDC", **kwargs)
        for name in ("create", "update", "delete", "activate", "persist", "write"):
            self.assertFalse(hasattr(store, name))


class CandidateCoreTests(unittest.TestCase):
    def setUp(self):
        from memory import init_store

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        init_store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def candidate_values(**overrides):
        values = {
            "type": "principle",
            "title": "Candidate memory",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "candidate-only content",
            "tags": ["candidate"],
        }
        values.update(overrides)
        return values

    def test_candidate_store_creates_real_candidate_with_path(self):
        from memory.candidate import CandidateStore
        from memory.store import MemoryStore

        candidates = CandidateStore(self.root, self.state)
        result = candidates.create(self.candidate_values(status="candidate"))

        self.assertIsInstance(result["candidate_id"], str)
        self.assertEqual(result["status"], "candidate")
        self.assertTrue(result["path"].startswith("memory/principles/"))
        self.assertTrue((self.root / result["path"]).is_file())
        self.assertEqual(MemoryStore(self.root).get(result["candidate_id"])["status"], "candidate")
        self.assertFalse(hasattr(candidates, "accept"))
        self.assertFalse(hasattr(candidates, "review"))

    def test_candidate_store_rejects_status_bypasses_before_writing(self):
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        before = list((self.root / "memory/principles").glob("*.md"))
        for values in (
            [],
            self.candidate_values(status="active"),
            self.candidate_values(status="deprecated"),
            self.candidate_values(status=None),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    candidates.create(values)
        self.assertEqual(list((self.root / "memory/principles").glob("*.md")), before)

    def test_candidate_store_rejects_active_and_unknown_fields_without_writing(self):
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        memory_dir = self.root / "memory/principles"
        journal = self.root / "state/distill_runs.jsonl"
        before_files = list(memory_dir.glob("*.md"))
        for extra in ({"active": True}, {"unknown": "value"}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    candidates.create(self.candidate_values(**extra))
        self.assertEqual(list(memory_dir.glob("*.md")), before_files)
        self.assertFalse(journal.exists())

    def test_candidate_store_structural_validation_precedes_backend(self):
        import memory_distill
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        invalid = []
        required = (
            "type",
            "title",
            "scope",
            "workspace",
            "confidentiality",
            "source",
            "content",
        )
        for field in required:
            missing = self.candidate_values()
            missing.pop(field)
            invalid.append(missing)
            for value in (None, True, "", "   "):
                invalid.append(self.candidate_values(**{field: value}))
        for field, value in (
            ("type", "unknown"),
            ("scope", "unknown"),
            ("workspace", "shared"),
            ("confidentiality", "secret"),
            ("action", "unknown"),
            ("action", True),
            ("confidence", "certain"),
            ("confidence", False),
            ("target_id", ""),
            ("project", True),
            ("context_id", []),
            ("source_id", "   "),
            ("evidence", ("ref",)),
            ("source_refs", True),
            ("relations", [""]),
            ("tags", ["valid", False]),
            ("source_path", "evidence.txt"),
            ("source_sha256", "a" * 64),
            ("source_path", "../outside.txt"),
            ("source_sha256", "not-a-sha256"),
        ):
            invalid.append(self.candidate_values(**{field: value}))

        with mock.patch.object(memory_distill, "apply_candidate") as apply:
            for values in invalid:
                with self.subTest(values=values):
                    with self.assertRaises(ValueError):
                        candidates.create(values)
            apply.assert_not_called()

    def test_candidate_store_rejects_unsafe_source_paths_before_backend(self):
        import hashlib
        import memory_distill
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        outside = Path(self.temp.name) / "outside-sensitive.txt"
        outside.write_text("outside-sensitive-content", encoding="utf-8")
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        symlink = self.root / "imports/manual/raw/sensitive-link.txt"
        symlink.symlink_to(outside)
        invalid = (
            self.candidate_values(
                source="file:test", source_path=str(outside), source_sha256=digest
            ),
            self.candidate_values(
                source="file:test",
                source_path="imports/manual/raw/sensitive-link.txt",
                source_sha256=digest,
            ),
        )
        memory_dir = self.root / "memory/principles"

        with mock.patch.object(memory_distill, "apply_candidate") as apply:
            for values in invalid:
                with self.subTest(source_path=values["source_path"]):
                    before = sorted(memory_dir.glob("*.md"))
                    with self.assertRaises(ValueError) as raised:
                        candidates.create(values)
                    self.assertEqual(sorted(memory_dir.glob("*.md")), before)
                    self.assertNotIn("outside-sensitive", str(raised.exception))
                    self.assertNotIn("sensitive-link", str(raised.exception))
            apply.assert_not_called()

    def test_candidate_store_rejects_invalid_store_before_backend_without_leakage(self):
        import memory_distill
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        secret_name = "private-patient-filename.md"
        secret_content = "private-patient-content"
        (self.root / "memory/principles" / secret_name).write_text(
            secret_content, encoding="utf-8"
        )

        with mock.patch.object(memory_distill, "apply_candidate") as apply:
            with self.assertRaises(ValueError) as raised:
                candidates.create(self.candidate_values())
            apply.assert_not_called()
        self.assertEqual(str(raised.exception), "memory store validation failed")
        self.assertNotIn(secret_name, str(raised.exception))
        self.assertNotIn(secret_content, str(raised.exception))

    def test_candidate_store_sanitizes_backend_exceptions(self):
        import memory_distill
        from memory.candidate import CandidateStore

        candidates = CandidateStore(self.root, self.state)
        sensitive = "restricted-filename-and-content"
        with mock.patch.object(
            memory_distill,
            "apply_candidate",
            side_effect=RuntimeError(sensitive),
        ) as apply:
            with self.assertRaises(ValueError) as raised:
                candidates.create(self.candidate_values())

        self.assertEqual(str(raised.exception), "candidate creation failed")
        self.assertNotIn(sensitive, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        formatted = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertNotIn(sensitive, formatted)
        apply.assert_called_once()

    def test_candidate_validation_uses_immutable_workspace_snapshot(self):
        import memory
        from memory.candidate import validate_candidate_values

        original = list(memory.WORKSPACE_CHOICES)
        try:
            memory.WORKSPACE_CHOICES.append("shared")
            with self.assertRaises(ValueError):
                validate_candidate_values(self.candidate_values(workspace="shared"))
        finally:
            memory.WORKSPACE_CHOICES[:] = original

    def test_memory_store_uses_immutable_workspace_snapshot(self):
        import memory
        from memory.store import MemoryStore

        original = list(memory.WORKSPACE_CHOICES)
        try:
            memory.WORKSPACE_CHOICES.append("shared")
            with self.assertRaises(ValueError):
                MemoryStore(self.root).active_relevant("PDC", workspace="shared")
        finally:
            memory.WORKSPACE_CHOICES[:] = original

    def test_memory_core_delegates_only_candidate_creation_and_reads(self):
        from memory.candidate import CandidateStore
        from memory.core import MemoryCore
        from memory.store import MemoryStore

        store = MemoryStore(self.root)
        core = MemoryCore(store, CandidateStore(self.root, self.state))
        result = core.create_candidate(self.candidate_values())

        self.assertEqual(core.get(result["candidate_id"])["status"], "candidate")
        self.assertEqual(core.search("candidate-only", workspace="personal"), [])
        with self.assertRaises(ValueError):
            core.search("candidate-only")
        for name in ("activate", "review", "persist", "create", "update", "delete"):
            self.assertFalse(hasattr(core, name))


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.state = Path(self.temp.name) / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def tearDown(self):
        self.temp.cleanup()

    def candidate_values(self, **overrides):
        values = {
            "type": "principle",
            "title": "Review candidate",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "candidate review content",
        }
        values.update(overrides)
        return values

    def create_candidate(self, **overrides):
        from memory.candidate import CandidateStore

        return CandidateStore(self.root, self.state).create(self.candidate_values(**overrides))[
            "candidate_id"
        ]

    def record(self, memory_id):
        from memory.store import MemoryStore

        return MemoryStore(self.root).get(memory_id)

    def make_review_agent(self):
        from agents.orchestrator import ReviewAgent
        from review.gate import ReviewGate

        return ReviewAgent(ReviewGate(self.root, self.state))

    def test_review_actions_are_exactly_accept_reject_merge_conflict(self):
        from review.gate import REVIEW_ACTIONS

        self.assertEqual(REVIEW_ACTIONS, frozenset({"accept", "reject", "merge", "conflict"}))

    def test_only_review_path_can_promote_candidate_to_active(self):
        import memory_distill
        from memory.candidate import CandidateStore
        from memory.core import MemoryCore
        from memory.store import MemoryStore
        from review.gate import ReviewGate

        core = MemoryCore(MemoryStore(self.root), CandidateStore(self.root, self.state))
        candidate_id = core.create_candidate(self.candidate_values())["candidate_id"]

        self.assertEqual(self.record(candidate_id)["status"], "candidate")
        for name in ("activate", "review", "persist", "update"):
            self.assertFalse(hasattr(core, name))
        with self.assertRaises(ValueError):
            ReviewGate(self.root, self.state).review("unknown", candidate_id)
        args = argparse.Namespace(root=str(self.root), state_dir=str(self.state), id=candidate_id)
        for call in (
            lambda: memory_distill._accept_candidate_impl(args),
            lambda: memory_distill._accept_candidate_impl(args, object()),
        ):
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(self.record(candidate_id)["status"], "candidate")

        result = self.make_review_agent().run(
            {"type": "memory.review", "input": {"action": "accept", "candidate_id": candidate_id}},
            {},
        )

        self.assertEqual(self.record(candidate_id)["status"], "active")
        self.assertEqual(result["output"]["status"], "completed")

    def test_lifecycle_validation_precedes_review_write(self):
        candidate_id = self.create_candidate()

        with mock.patch(
            "memory.lifecycle.validate_transition",
            side_effect=ValueError("invalid memory state transition"),
        ) as validate_transition:
            with self.assertRaisesRegex(ValueError, "invalid memory state transition"):
                self.make_review_agent().run(
                    {
                        "type": "memory.review",
                        "input": {"action": "accept", "candidate_id": candidate_id},
                    },
                    {},
                )

        validate_transition.assert_called_once_with("candidate", "active")
        self.assertEqual(self.record(candidate_id)["status"], "candidate")

    def test_conflict_action_marks_candidate_conflicted_canonically(self):
        write_memory(
            self.root,
            id="review-target",
            title="Review target",
            content="confirmed target",
        )
        candidate_id = self.create_candidate(
            action="conflict",
            title="Conflict review candidate",
            source="codex",
            confidence="inferred",
            content="conflicting candidate content",
            target_id="review-target",
            source_refs=["manual:test-evidence"],
        )

        result = self.make_review_agent().run(
            {"type": "memory.review", "input": {"action": "conflict", "candidate_id": candidate_id}},
            {},
        )

        self.assertEqual(self.record(candidate_id)["status"], "conflicted")
        self.assertEqual(result["output"]["status"], "conflicted")
        self.assertEqual(self.record("review-target")["content"], "confirmed target")

    def test_gate_requires_explicit_merge_or_conflict_actions(self):
        from review.gate import ReviewGate

        candidates = {}
        for action in ("merge", "support", "conflict"):
            target_id = f"explicit-{action}-target"
            write_memory(
                self.root,
                id=target_id,
                title=f"Explicit {action} target",
                content=f"stable {action} target",
            )
            candidates[action] = self.create_candidate(
                action=action,
                title=f"{action} candidate",
                content=f"{action} candidate content",
                source="codex",
                confidence="inferred",
                target_id=target_id,
                source_refs=[f"manual:{action}"],
            )

        gate = ReviewGate(self.root, self.state)
        for candidate_id in candidates.values():
            with self.subTest(candidate_id=candidate_id):
                with self.assertRaises(ValueError):
                    gate.review("accept", candidate_id)
                self.assertEqual(self.record(candidate_id)["status"], "candidate")

        gate.review("merge", candidates["merge"])
        gate.review("merge", candidates["support"])
        gate.review("conflict", candidates["conflict"])
        self.assertEqual(self.record(candidates["merge"])["status"], "archived")
        self.assertEqual(self.record(candidates["support"])["status"], "archived")
        self.assertEqual(self.record(candidates["conflict"])["status"], "conflicted")

    def test_reject_without_reason_writes_stable_default(self):
        candidate_id = self.create_candidate()

        result = self.make_review_agent().run(
            {
                "type": "memory.review",
                "input": {"action": "reject", "candidate_id": candidate_id},
            },
            {},
        )

        self.assertEqual(result["output"]["status"], "archived")
        self.assertEqual(self.record(candidate_id)["review_reason"], "rejected by reviewer")

    def test_public_legacy_review_handlers_translate_to_canonical_actions(self):
        import memory_distill

        cases = [
            ({"requested_action": "create", "candidate_action": "ADD"}, "accept"),
            ({"requested_action": "merge", "candidate_action": "UPDATE"}, "merge"),
            ({"requested_action": "support", "candidate_action": "UPDATE"}, "merge"),
            ({"requested_action": "conflict", "candidate_action": "REVIEW_REQUIRED"}, "conflict"),
            ({"requested_action": "create", "candidate_action": "REVIEW_REQUIRED"}, "conflict"),
        ]
        for record, expected_action in cases:
            with self.subTest(record=record), mock.patch.object(
                memory_distill, "_candidate_record", return_value=record
            ), mock.patch(
                "review.gate.ReviewGate.review", return_value={"status": "completed"}
            ) as review:
                accepted = memory_distill.accept_candidate(
                    argparse.Namespace(
                        root=str(self.root),
                        state_dir=str(self.state),
                        id="legacy-candidate",
                    )
                )

            self.assertEqual(accepted, {"status": "completed"})
            review.assert_called_once_with(expected_action, "legacy-candidate", reason=None)

        candidate_id = self.create_candidate(title="Reject candidate")
        with mock.patch("review.gate.ReviewGate.review", return_value={"status": "archived"}) as review:
            rejected = memory_distill.reject_candidate(
                argparse.Namespace(root=str(self.root), id=candidate_id, reason="duplicate")
            )

        self.assertEqual(rejected, {"status": "archived"})
        review.assert_called_once_with("reject", candidate_id, reason="duplicate")

    def test_merge_requires_state_dir_before_candidate_introspection(self):
        from review.gate import ReviewGate

        candidate_id = self.create_candidate()

        with self.assertRaisesRegex(ValueError, "state_dir is required"):
            ReviewGate(self.root).review("merge", candidate_id)


class PartitionIsolationTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def tearDown(self):
        self.temp.cleanup()

    def candidate_values(self, **overrides):
        values = {
            "type": "principle",
            "title": "Partition candidate",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "partition candidate content",
        }
        values.update(overrides)
        return values

    def candidate_store(self):
        from memory.candidate import CandidateStore

        return CandidateStore(self.root, self.state)

    def create_cross_workspace_transition(self):
        import memory

        write_memory(
            self.root,
            id="personal-context-source",
            type="context",
            title="Personal context source",
            scope="context",
            context_id="personal-source",
            valid_from="2023-09-01",
            content="personal context content",
        )
        return memory.context_transition(
            argparse.Namespace(
                root=str(self.root),
                from_context="personal-source",
                to_context="planned-work",
                to_title="Planned work context",
                workspace="work",
                confidentiality="restricted",
                effective_date="2027-07-01",
                reason="move to work",
                source="user",
                confidence="confirmed",
                content=None,
                tags=[],
                dry_run=False,
            )
        )

    def test_cross_partition_targets_are_rejected_during_candidate_creation(self):
        targets = [
            ("cross-work-target", "personal", "personal"),
            ("cross-restricted-target", "work", "internal"),
        ]
        for target_id, workspace, confidentiality in targets:
            write_memory(
                self.root,
                id=target_id,
                title=target_id,
                content=f"secret content for {target_id}",
                workspace="work",
                confidentiality="restricted",
            )
            target_path = next((self.root / "memory").rglob(f"{target_id}-*.md"))
            before = target_path.read_bytes()
            for action in ("UPDATE", "DEPRECATE", "merge", "support", "conflict"):
                with self.subTest(target=target_id, action=action):
                    with self.assertRaises(ValueError) as raised:
                        self.candidate_store().create(
                            self.candidate_values(
                                action=action,
                                title=f"{target_id} {action}",
                                content=f"candidate {target_id} {action}",
                                workspace=workspace,
                                confidentiality=confidentiality,
                                target_id=target_id,
                            )
                        )
                    error = str(raised.exception)
                    self.assertNotIn(target_id, error)
                    self.assertNotIn("secret content", error)
                    self.assertEqual(target_path.read_bytes(), before)

    def test_review_rechecks_partition_after_candidate_file_tampering(self):
        from review.gate import ReviewGate

        write_memory(self.root, id="safe-target", title="Safe target", content="safe target")
        write_memory(
            self.root,
            id="restricted-target",
            title="Restricted target",
            content="restricted target content",
            workspace="work",
            confidentiality="restricted",
        )
        created = self.candidate_store().create(
            self.candidate_values(
                action="merge",
                target_id="safe-target",
                source="codex",
                confidence="inferred",
                source_refs=["manual:partition"],
            )
        )
        candidate_path = self.root / created["path"]
        safe_path = next((self.root / "memory").rglob("safe-target-*.md"))
        restricted_path = next((self.root / "memory").rglob("restricted-target-*.md"))
        text = candidate_path.read_text(encoding="utf-8")
        text = text.replace('target_id: "safe-target"', 'target_id: "restricted-target"')
        text = text.replace(
            hashlib.sha256(safe_path.read_bytes()).hexdigest(),
            hashlib.sha256(restricted_path.read_bytes()).hexdigest(),
        )
        candidate_path.write_text(text, encoding="utf-8")
        before_candidate = candidate_path.read_bytes()
        before_target = restricted_path.read_bytes()

        with self.assertRaises(ValueError) as raised:
            ReviewGate(self.root, self.state).review("merge", created["candidate_id"])

        error = str(raised.exception)
        self.assertNotIn("restricted-target", error)
        self.assertNotIn("restricted target content", error)
        self.assertEqual(candidate_path.read_bytes(), before_candidate)
        self.assertEqual(restricted_path.read_bytes(), before_target)

    def test_context_transition_tampering_cannot_retarget_restricted_context(self):
        import memory
        from review.gate import ReviewGate

        transition = self.create_cross_workspace_transition()
        candidate_path = self.root / transition["transition_path"]
        write_memory(
            self.root,
            id="restricted-context-target",
            type="context",
            title="Restricted context target",
            scope="context",
            context_id="restricted-existing",
            valid_from="2024-01-01",
            content="restricted context content",
            workspace="work",
            confidentiality="restricted",
        )
        target_path = next(
            (self.root / "memory").rglob("restricted-context-target-*.md")
        )
        candidate, errors = memory.parse_front_matter(candidate_path)
        self.assertEqual(errors, [])
        new_record = json.loads(candidate["new_record_json"])
        new_record.update(
            {
                "id": "tampered-new-context",
                "title": "Tampered new context",
                "supersedes": ["restricted-context-target"],
            }
        )
        candidate["target_id"] = "restricted-context-target"
        candidate["expected_target_sha256"] = hashlib.sha256(
            target_path.read_bytes()
        ).hexdigest()
        candidate["new_record_json"] = json.dumps(
            new_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        candidate_path.write_text(
            memory.render_existing_memory(candidate_path, candidate), encoding="utf-8"
        )
        new_path = (
            self.root
            / memory.TYPE_DIRS["context"]
            / f"{new_record['id']}-{memory.safe_slug(new_record['title'])}.md"
        )
        before_candidate = candidate_path.read_bytes()
        before_target = target_path.read_bytes()

        with self.assertRaises(ValueError) as raised:
            ReviewGate(self.root, self.state).review("accept", candidate["id"])

        error = str(raised.exception)
        self.assertNotIn("restricted-context-target", error)
        self.assertNotIn("restricted context content", error)
        self.assertEqual(candidate_path.read_bytes(), before_candidate)
        self.assertEqual(target_path.read_bytes(), before_target)
        self.assertFalse(new_path.exists())

    def test_context_transition_rejects_proposal_input_hash_mismatch(self):
        import memory
        from review.gate import ReviewGate

        transition = self.create_cross_workspace_transition()
        candidate_path = self.root / transition["transition_path"]
        source_path = next((self.root / "memory").rglob("personal-context-source-*.md"))
        candidate, errors = memory.parse_front_matter(candidate_path)
        self.assertEqual(errors, [])
        candidate["proposal_input_hash"] = "0" * 64
        candidate_path.write_text(
            memory.render_existing_memory(candidate_path, candidate), encoding="utf-8"
        )
        before_candidate = candidate_path.read_bytes()
        before_source = source_path.read_bytes()
        new_path = self.root / transition["new_context_path"]

        with self.assertRaises(ValueError):
            ReviewGate(self.root, self.state).review("accept", candidate["id"])

        self.assertEqual(candidate_path.read_bytes(), before_candidate)
        self.assertEqual(source_path.read_bytes(), before_source)
        self.assertFalse(new_path.exists())

    def test_context_transition_rejects_invalid_hashed_new_record_safely(self):
        import memory
        from review.gate import ReviewGate

        transition = self.create_cross_workspace_transition()
        candidate_path = self.root / transition["transition_path"]
        candidate, errors = memory.parse_front_matter(candidate_path)
        self.assertEqual(errors, [])
        new_record = json.loads(candidate["new_record_json"])
        new_record["unexpected_private_field"] = "hidden"
        candidate["new_record_json"] = json.dumps(
            new_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        proposal_input = {
            "from_context": candidate["from_context"],
            "to_context": candidate["to_context"],
            "effective_date": candidate["effective_date"],
            "reason": candidate["reason"],
            "source": candidate["source"],
            "confidence": candidate["confidence"],
            "target_id": candidate["target_id"],
            "target_sha256": candidate["expected_target_sha256"],
            "new_record": new_record,
        }
        candidate["proposal_input_hash"] = hashlib.sha256(
            json.dumps(
                proposal_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        candidate_path.write_text(
            memory.render_existing_memory(candidate_path, candidate), encoding="utf-8"
        )
        before_candidate = candidate_path.read_bytes()

        with self.assertRaises(ValueError) as raised:
            ReviewGate(self.root, self.state).review("accept", candidate["id"])

        self.assertEqual(str(raised.exception), "context transition proposal is invalid")
        self.assertEqual(candidate_path.read_bytes(), before_candidate)
        self.assertFalse((self.root / transition["new_context_path"]).exists())

    def test_context_transition_allows_personal_to_work_partition(self):
        import memory
        from memory.store import MemoryStore
        from review.gate import ReviewGate

        transition = self.create_cross_workspace_transition()
        candidate_path = self.root / transition["transition_path"]
        candidate, errors = memory.parse_front_matter(candidate_path)
        self.assertEqual(errors, [])

        ReviewGate(self.root, self.state).review("accept", candidate["id"])

        records = {row["id"]: row for row in MemoryStore(self.root).records()}
        self.assertEqual(records["personal-context-source"]["status"], "deprecated")
        self.assertEqual(records[candidate["id"]]["status"], "archived")
        planned = next(row for row in records.values() if row.get("context_id") == "planned-work")
        self.assertEqual(planned["status"], "active")
        self.assertEqual(
            (planned["workspace"], planned["confidentiality"]),
            ("work", "restricted"),
        )

    def test_context_transition_rejects_work_to_personal_partition(self):
        import memory
        from review.gate import ReviewGate

        write_memory(
            self.root,
            id="work-context-source",
            type="context",
            title="Work context source",
            scope="context",
            context_id="work-source",
            valid_from="2023-09-01",
            content="work context content",
            workspace="work",
            confidentiality="internal",
        )
        transition = memory.context_transition(
            argparse.Namespace(
                root=str(self.root),
                from_context="work-source",
                to_context="planned-personal",
                to_title="Planned personal context",
                workspace="personal",
                confidentiality="personal",
                effective_date="2027-07-01",
                reason="move to personal",
                source="user",
                confidence="confirmed",
                content=None,
                tags=[],
                dry_run=False,
            )
        )
        candidate_path = self.root / transition["transition_path"]
        source_path = next((self.root / "memory").rglob("work-context-source-*.md"))
        candidate, errors = memory.parse_front_matter(candidate_path)
        self.assertEqual(errors, [])
        before_candidate = candidate_path.read_bytes()
        before_source = source_path.read_bytes()

        with self.assertRaises(ValueError):
            ReviewGate(self.root, self.state).review("accept", candidate["id"])

        self.assertEqual(candidate_path.read_bytes(), before_candidate)
        self.assertEqual(source_path.read_bytes(), before_source)
        self.assertFalse((self.root / transition["new_context_path"]).exists())

    def test_restricted_records_do_not_affect_candidate_deduplication(self):
        for index, (workspace, confidentiality) in enumerate(
            (("personal", "personal"), ("work", "internal")), start=1
        ):
            source = self.root / f"imports/manual/text/partition-{index}.txt"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(f"partition evidence {index}", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            restricted_id = f"restricted-dedup-{index}"
            title = f"Restricted duplicate {index}"
            content = f"restricted duplicate content {index}"
            relative_source = source.relative_to(self.root).as_posix()
            write_memory(
                self.root,
                id=restricted_id,
                title=title,
                content=content,
                workspace="work",
                confidentiality="restricted",
                source_path=relative_source,
                source_sha256=digest,
            )

            created = self.candidate_store().create(
                self.candidate_values(
                    title=title,
                    content=content,
                    workspace=workspace,
                    confidentiality=confidentiality,
                    source="codex",
                    confidence="inferred",
                    source_path=relative_source,
                    source_sha256=digest,
                )
            )

            self.assertNotEqual(created["candidate_id"], restricted_id)
            self.assertEqual(created["status"], "candidate")

    def test_project_scope_requires_active_project_in_same_partition(self):
        for index, (workspace, confidentiality) in enumerate(
            (("personal", "personal"), ("work", "internal")), start=1
        ):
            project = f"restricted-project-{index}"
            write_memory(
                self.root,
                id=f"restricted-project-memory-{index}",
                type="project",
                title=f"Restricted project {index}",
                scope="project",
                project=project,
                content=f"restricted project content {index}",
                workspace="work",
                confidentiality="restricted",
            )

            with self.assertRaises(ValueError) as raised:
                self.candidate_store().create(
                    self.candidate_values(
                        title=f"Project candidate {index}",
                        content=f"project candidate content {index}",
                        scope="project",
                        project=project,
                        workspace=workspace,
                        confidentiality=confidentiality,
                    )
                )

            self.assertNotIn(project, str(raised.exception))

    def test_context_reference_requires_context_in_same_partition_at_creation(self):
        write_memory(
            self.root,
            id="restricted-context-reference",
            type="context",
            title="Restricted context reference",
            scope="context",
            context_id="restricted-context-id",
            valid_from="2024-01-01",
            content="restricted context attribute",
            workspace="work",
            confidentiality="restricted",
        )
        referenced_path = next(
            (self.root / "memory").rglob("restricted-context-reference-*.md")
        )
        before = referenced_path.read_bytes()

        with self.assertRaises(ValueError) as raised:
            self.candidate_store().create(
                self.candidate_values(context_id="restricted-context-id")
            )

        error = str(raised.exception)
        self.assertNotIn("restricted-context-id", error)
        self.assertNotIn("restricted context attribute", error)
        self.assertEqual(referenced_path.read_bytes(), before)

    def test_review_rechecks_tampered_project_and_context_references(self):
        import memory
        from review.gate import ReviewGate

        write_memory(
            self.root,
            id="restricted-project-reference",
            type="project",
            title="Restricted project reference",
            scope="project",
            project="restricted-project-id",
            content="restricted project attribute",
            workspace="work",
            confidentiality="restricted",
        )
        write_memory(
            self.root,
            id="restricted-context-reference",
            type="context",
            title="Restricted context reference",
            scope="context",
            context_id="restricted-context-id",
            valid_from="2024-01-01",
            content="restricted context attribute",
            workspace="work",
            confidentiality="restricted",
        )
        references = {
            "project": next(
                (self.root / "memory").rglob("restricted-project-reference-*.md")
            ),
            "context": next(
                (self.root / "memory").rglob("restricted-context-reference-*.md")
            ),
        }
        candidates = {}
        for kind in references:
            created = self.candidate_store().create(
                self.candidate_values(
                    title=f"Tampered {kind} candidate",
                    content=f"tampered {kind} candidate content",
                )
            )
            path = self.root / created["path"]
            record, errors = memory.parse_front_matter(path)
            self.assertEqual(errors, [])
            if kind == "project":
                record["scope"] = "project"
                record["project"] = "restricted-project-id"
            else:
                record["context_id"] = "restricted-context-id"
            path.write_text(memory.render_existing_memory(path, record), encoding="utf-8")
            candidates[kind] = (created["candidate_id"], path, path.read_bytes())
        reference_bytes = {kind: path.read_bytes() for kind, path in references.items()}

        gate = ReviewGate(self.root, self.state)
        for kind, (candidate_id, path, before_candidate) in candidates.items():
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError) as raised:
                    gate.review("accept", candidate_id)
                error = str(raised.exception)
                self.assertNotIn(f"restricted-{kind}-id", error)
                self.assertNotIn(f"restricted {kind} attribute", error)
                self.assertEqual(path.read_bytes(), before_candidate)
                self.assertEqual(references[kind].read_bytes(), reference_bytes[kind])

    def test_same_partition_project_and_context_references_can_activate(self):
        from memory.store import MemoryStore
        from review.gate import ReviewGate

        write_memory(
            self.root,
            id="personal-project-reference",
            type="project",
            title="Personal project reference",
            scope="project",
            project="personal-project-id",
            content="personal project",
        )
        write_memory(
            self.root,
            id="personal-context-reference",
            type="context",
            title="Personal context reference",
            scope="context",
            context_id="personal-context-id",
            valid_from="2024-01-01",
            content="personal context",
        )
        created = self.candidate_store().create(
            self.candidate_values(
                scope="project",
                project="personal-project-id",
                context_id="personal-context-id",
            )
        )

        ReviewGate(self.root, self.state).review("accept", created["candidate_id"])

        self.assertEqual(
            MemoryStore(self.root).get(created["candidate_id"])["status"], "active"
        )


class RecordingGate:
    def __init__(self):
        self.calls = []

    def review(self, action, candidate_id, reason=None, workspace=None):
        self.calls.append((action, candidate_id, reason, workspace))
        return {"status": "reviewed"}


class RecordingBuilder:
    def __init__(self):
        self.calls = []

    def empty(self, limit):
        self.calls.append(("empty", limit))
        return {"text": "", "sources": [], "limit": limit, "used": 0}

    def build(self, query, limit, workspace=None, project=None):
        self.calls.append((query, limit, workspace, project))
        return {"text": query, "limit": limit}


class FakeContextStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def active_relevant(self, query, workspace=None, project=None):
        self.calls.append((query, workspace, project))
        return list(self.rows)

    def records(self):
        raise AssertionError("ContextBuilder must not read the full store")


class ConcreteAgentTests(unittest.TestCase):
    def setUp(self):
        self.gate = RecordingGate()
        self.builder = RecordingBuilder()

    def test_review_and_context_agents_delegate_and_return_strict_envelopes(self):
        from agents.orchestrator import ContextAgent, ReviewAgent

        agents = [ReviewAgent(self.gate), ContextAgent(self.builder)]
        tasks = [
            {
                "type": "memory.review",
                "input": {
                    "action": "reject",
                    "candidate_id": "candidate-1",
                    "reason": "duplicate",
                },
            },
            {
                "type": "context.build",
                "context_limit": 120,
                "input": {
                    "task": {
                        "type": "memory.search",
                        "input": {
                            "query": "original query",
                            "workspace": "work",
                            "project": "p1",
                        },
                    }
                },
            },
        ]

        results = [agent.run(task, {}) for agent, task in zip(agents, tasks)]

        for agent, task, result in zip(agents, tasks, results):
            self.assertIs(validate_result(result, agent, task), result)
            self.assertIs(result["requires_review"], True)
        self.assertEqual(
            self.gate.calls,
            [("reject", "candidate-1", "duplicate", None)],
        )
        self.assertEqual(self.builder.calls, [("original query", 120, "work", "p1")])

    def test_context_agent_query_falls_back_to_task_then_type(self):
        from agents.orchestrator import ContextAgent

        agent = ContextAgent(self.builder)
        agent.run(
            {
                "type": "context.build",
                "input": {
                    "task": {
                        "type": "memory.create",
                        "input": {"task": "draft paper", "workspace": "personal"},
                    }
                },
            },
            {},
        )
        agent.run(
            {
                "type": "context.build",
                "input": {
                    "task": {
                        "type": "memory.search",
                        "input": {"workspace": "work"},
                    }
                },
            },
            {},
        )

        self.assertEqual(self.builder.calls[0][0], "draft paper")
        self.assertEqual(self.builder.calls[1][0], "memory.search")

    def test_context_agent_requires_workspace_before_builder_call(self):
        from agents.orchestrator import ContextAgent

        builder = RecordingBuilder()
        agent = ContextAgent(builder)
        invalid_workspaces = (None, "", "   ", "shared", "other")
        tasks = []
        tasks.append({"type": "context.build", "input": {"query": "direct"}})
        tasks.append(
            {"type": "context.build", "workspace": "shared", "input": {"query": "direct"}}
        )
        tasks.append(
            {
                "type": "context.build",
                "input": {
                    "task": {"type": "memory.search", "input": {"query": "embedded"}}
                },
            }
        )
        for workspace in invalid_workspaces:
            tasks.append(
                {
                    "type": "context.build",
                    "input": {"query": "direct", "workspace": workspace},
                }
            )
            tasks.append(
                {
                    "type": "context.build",
                    "input": {
                        "task": {
                            "type": "memory.search",
                            "input": {"query": "embedded", "workspace": workspace},
                        }
                    },
                }
            )

        for task in tasks:
            with self.subTest(task=task):
                with self.assertRaises(ValueError):
                    agent.run(task, {})
        self.assertEqual(builder.calls, [])

    def test_context_agent_legacy_embedded_tasks_without_workspace_still_call_builder(self):
        from agents.orchestrator import ContextAgent

        agent = ContextAgent(self.builder)

        for task_type in ("import.file", "import.chatgpt", "memory.review"):
            task = {"type": "context.build", "input": {"task": {"type": task_type, "input": {}}}}
            result = agent.run(task, {})
            self.assertIs(validate_result(result, agent, task), result)

        self.assertEqual(
            self.builder.calls,
            [
                ("empty", 8000),
                ("empty", 8000),
                ("empty", 8000),
            ],
        )

    def test_context_agent_accepts_build_only_adapter_for_scoped_tasks(self):
        from agents.orchestrator import ContextAgent

        class BuildOnlyBuilder:
            def build(self, query, limit, workspace=None, project=None):
                return {"text": query, "sources": [], "limit": limit, "used": len(query)}

        agent = ContextAgent(BuildOnlyBuilder())
        task = {"type": "context.build", "input": {"query": "PDC", "workspace": "work"}}

        result = agent.run(task, {})

        self.assertIs(validate_result(result, agent, task), result)
        self.assertEqual(result["output"]["text"], "PDC")

    def test_context_agent_prefers_top_level_metadata_and_keeps_input_compatibility(self):
        from agents.orchestrator import ContextAgent

        agent = ContextAgent(self.builder)
        agent.run(
            {
                "type": "context.build",
                "workspace": "work",
                "project": "top-level-project",
                "context_limit": 90,
                "input": {
                    "query": "direct query",
                    "workspace": "personal",
                    "project": "input-project",
                },
            },
            {},
        )
        agent.run(
            {
                "type": "context.build",
                "context_limit": 91,
                "input": {
                    "task": {
                        "type": "memory.search",
                        "workspace": "work",
                        "project": "embedded-top-level",
                        "input": {
                            "query": "embedded query",
                            "workspace": "personal",
                            "project": "embedded-input",
                        },
                    }
                },
            },
            {},
        )
        agent.run(
            {
                "type": "context.build",
                "context_limit": 92,
                "input": {
                    "task": {
                        "type": "memory.search",
                        "input": {
                            "query": "compatibility query",
                            "workspace": "personal",
                            "project": "compatibility-project",
                        },
                    }
                },
            },
            {},
        )

        self.assertEqual(
            self.builder.calls,
            [
                ("direct query", 90, "work", "top-level-project"),
                ("embedded query", 91, "work", "embedded-top-level"),
                ("compatibility query", 92, "personal", "compatibility-project"),
            ],
        )

    def test_review_and_context_agents_reject_malformed_tasks(self):
        from agents.orchestrator import ContextAgent, ReviewAgent

        malformed = [
            (
                ReviewAgent(self.gate),
                {"type": "memory.review", "input": {"action": True, "candidate_id": "x"}},
            ),
            (
                ReviewAgent(self.gate),
                {
                    "type": "memory.review",
                    "input": {"action": "conflict", "candidate_id": "x", "legacy": True},
                },
            ),
            (
                ContextAgent(self.builder),
                {"type": "context.build", "input": {"query": "PDC", "include_inactive": True}},
            ),
        ]
        for agent, task in malformed:
            with self.subTest(agent=agent.agent_id, task=task):
                with self.assertRaises(ValueError):
                    agent.run(task, {})


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        from memory import init_store

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        init_store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_context_is_active_relevant_bounded_and_restricted_safe(self):
        from context import ContextBuilder
        from memory.store import MemoryStore

        write_memory(
            self.root,
            id="ctx-active",
            workspace="work",
            confidentiality="internal",
            title="PDC active",
            content="PDC active evidence",
        )
        write_memory(
            self.root,
            id="ctx-candidate",
            workspace="work",
            confidentiality="internal",
            status="candidate",
            title="PDC candidate",
            content="PDC candidate evidence",
        )
        write_memory(
            self.root,
            id="ctx-restricted",
            workspace="work",
            confidentiality="restricted",
            title="PDC restricted",
            content="PDC restricted secret",
        )
        write_memory(
            self.root,
            id="ctx-unrelated",
            workspace="work",
            confidentiality="internal",
            title="Other topic",
            content="unrelated material",
        )

        pack = ContextBuilder(MemoryStore(self.root)).build(
            "PDC evidence",
            limit=120,
            workspace="work",
        )

        self.assertEqual(pack["limit"], 120)
        self.assertEqual(pack["used"], len(pack["text"]))
        self.assertLessEqual(pack["used"], 120)
        self.assertIn("PDC active", pack["text"])
        self.assertNotIn("PDC candidate", pack["text"])
        self.assertNotIn("PDC restricted", pack["text"])
        self.assertNotIn("unrelated material", pack["text"])
        self.assertEqual(pack["sources"], ["ctx-active"])

    def test_context_is_deterministic_and_uses_only_active_relevant_results(self):
        from context import ContextBuilder

        store = FakeContextStore(
            [
                {
                    "id": "ctx-1",
                    "title": "PDC alpha",
                    "content": "first evidence",
                    "tags": ["alpha", "beta"],
                },
                {
                    "id": "ctx-2",
                    "title": "PDC beta",
                    "content": "second evidence",
                    "tags": [],
                },
            ]
        )
        builder = ContextBuilder(store)

        first = builder.build("PDC", limit=400, workspace="work", project="proj-1")
        second = builder.build("PDC", limit=400, workspace="work", project="proj-1")

        self.assertEqual(first, second)
        self.assertEqual(store.calls, [("PDC", "work", "proj-1"), ("PDC", "work", "proj-1")])
        self.assertEqual(first["sources"], ["ctx-1", "ctx-2"])

    def test_context_normalizes_whitespace_and_skips_duplicate_entries(self):
        from context import ContextBuilder

        store = FakeContextStore(
            [
                {
                    "id": "ctx-1",
                    "title": "  PDC   alpha  ",
                    "content": "first   evidence",
                    "tags": ["a", "b"],
                },
                {
                    "id": "ctx-1",
                    "title": "PDC alpha",
                    "content": "first evidence",
                    "tags": ["a", "b"],
                },
                {
                    "id": "ctx-2",
                    "title": "PDC   alpha",
                    "content": " first evidence ",
                    "tags": ["a", "b"],
                },
                {
                    "id": "ctx-3",
                    "title": "PDC beta",
                    "content": "second evidence",
                    "tags": ["x"],
                },
            ]
        )

        pack = ContextBuilder(store).build("PDC", limit=400, workspace="work")

        self.assertEqual(
            pack,
            {
                "text": "id: ctx-1\ntitle: PDC alpha\ncontent: first evidence\ntags: a, b\n\n"
                "id: ctx-3\ntitle: PDC beta\ncontent: second evidence\ntags: x",
                "sources": ["ctx-1", "ctx-3"],
                "limit": 400,
                "used": 121,
            },
        )

    def test_context_builder_rejects_invalid_limit_and_has_compatibility_re_exports(self):
        from context import ContextBuilder

        builder = ContextBuilder(FakeContextStore([]))

        for limit in (True, 0, -1, 1.5, "8000", None):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    builder.build("PDC", limit=limit, workspace="work")

        package = (SOURCE_DIR / "context" / "__init__.py").read_text(encoding="utf-8")
        compatibility = (SOURCE_DIR / "context.py").read_text(encoding="utf-8")
        self.assertEqual(
            package.strip(),
            'from .builder import ContextBuilder\n\n\n__all__ = ["ContextBuilder"]',
        )
        self.assertEqual(
            compatibility.strip(),
            'from context.builder import ContextBuilder\n\n\n__all__ = ["ContextBuilder"]',
        )

    def test_context_builder_rejects_missing_workspace(self):
        from context import ContextBuilder

        builder = ContextBuilder(FakeContextStore([]))

        with self.assertRaisesRegex(ValueError, "workspace must be a non-empty string"):
            builder.build("PDC", workspace=None)
        self.assertEqual(builder.store.calls, [])

    def test_context_agent_and_builder_work_with_real_store(self):
        from agents.orchestrator import ContextAgent
        from context import ContextBuilder
        from memory.store import MemoryStore

        write_memory(
            self.root,
            id="ctx-agent-active",
            workspace="work",
            confidentiality="internal",
            title="PDC review",
            content="agent-visible evidence",
        )
        write_memory(
            self.root,
            id="ctx-agent-restricted",
            workspace="work",
            confidentiality="restricted",
            title="PDC secret",
            content="must stay hidden",
        )

        agent = ContextAgent(ContextBuilder(MemoryStore(self.root)))
        task = {
            "type": "context.build",
            "context_limit": 200,
            "input": {
                "task": {
                    "type": "memory.search",
                    "input": {"query": "PDC", "workspace": "work"},
                }
            },
        }

        result = agent.run(task, {})

        self.assertIs(validate_result(result, agent, task), result)
        self.assertEqual(result["output"]["sources"], ["ctx-agent-active"])
        self.assertIn("PDC review", result["output"]["text"])
        self.assertNotIn("PDC secret", result["output"]["text"])


class RealAgentStorageTests(unittest.TestCase):
    def setUp(self):
        from memory import init_store

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        init_store(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def make_core(self):
        from memory.candidate import CandidateStore
        from memory.core import MemoryCore
        from memory.store import MemoryStore

        return MemoryCore(
            MemoryStore(self.root),
            CandidateStore(self.root, self.state),
        )

    @staticmethod
    def candidate_values(**overrides):
        values = {
            "type": "principle",
            "title": "Agent candidate",
            "scope": "global",
            "workspace": "personal",
            "confidentiality": "personal",
            "source": "manual:user_confirmed",
            "confidence": "confirmed",
            "content": "real Agent candidate content",
            "tags": ["agent"],
        }
        values.update(overrides)
        return values

    def test_real_memory_agent_creates_candidate_file_and_rejects_unknown_keys(self):
        from agents.orchestrator import MemoryAgent

        agent = MemoryAgent(self.make_core())
        task = {"type": "memory.create", "input": self.candidate_values()}
        result = agent.run(task, {})

        self.assertIs(validate_result(result, agent, task), result)
        self.assertEqual(len(result["candidates"]), 1)
        candidate_id = result["candidates"][0]
        self.assertEqual(agent.core.get(candidate_id)["status"], "candidate")
        self.assertTrue((self.root / result["output"]["path"]).is_file())
        self.assertEqual(agent.agent_id, "memory_agent")
        self.assertEqual(agent.handles, ["memory.create"])

        memory_dir = self.root / "memory/principles"
        journal = self.root / "state/distill_runs.jsonl"
        invalid = (
            {"active": True},
            {"unsupported": "value"},
            {"status": "active"},
            {"status": None},
            {"status": "deprecated"},
        )
        for extra in invalid:
            with self.subTest(extra=extra):
                before_files = sorted(memory_dir.glob("*.md"))
                before_journal = journal.read_bytes()
                with self.assertRaises(ValueError):
                    agent.run(
                        {"type": "memory.create", "input": self.candidate_values(**extra)},
                        {},
                    )
                self.assertEqual(sorted(memory_dir.glob("*.md")), before_files)
                self.assertEqual(journal.read_bytes(), before_journal)

    def test_real_memory_agent_injects_top_level_workspace_and_project(self):
        from agents.orchestrator import MemoryAgent

        write_memory(
            self.root,
            id="active-project",
            type="project",
            scope="project",
            project="p-top-level",
            title="Top level project",
            content="project evidence",
        )
        agent = MemoryAgent(self.make_core())
        values = self.candidate_values()
        values.pop("workspace")
        task = {
            "type": "memory.create",
            "workspace": "personal",
            "project": "p-top-level",
            "input": values,
        }

        result = agent.run(task, {})
        record = agent.core.get(result["candidates"][0])

        self.assertEqual(record["workspace"], "personal")
        self.assertEqual(record["project"], "p-top-level")

    def test_real_search_agent_returns_only_active_relevant_safe_rows(self):
        from agents.orchestrator import SearchAgent

        write_memory(
            self.root,
            id="search-active",
            title="PDC active",
            content="PDC safe evidence",
        )
        write_memory(
            self.root,
            id="search-restricted",
            title="PDC restricted",
            content="PDC restricted evidence",
            workspace="work",
            confidentiality="restricted",
        )
        write_memory(
            self.root,
            id="search-work",
            title="PDC work",
            content="PDC work evidence",
            workspace="work",
            confidentiality="internal",
        )
        write_memory(
            self.root,
            id="search-candidate",
            title="PDC candidate",
            content="PDC inactive evidence",
            status="candidate",
        )
        write_memory(
            self.root,
            id="search-unrelated",
            title="Other active",
            content="unrelated material",
        )
        agent = SearchAgent(self.make_core())
        task = {
            "type": "memory.search",
            "input": {"query": "pdc", "workspace": "personal"},
        }

        result = agent.run(task, {})

        self.assertIs(validate_result(result, agent, task), result)
        self.assertEqual(
            [record["id"] for record in result["output"]["results"]],
            ["search-active"],
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(agent.agent_id, "search_agent")
        self.assertEqual(agent.handles, ["memory.search"])

        work_task = {
            "type": "memory.search",
            "input": {"query": "pdc", "workspace": "work"},
        }
        work_result = agent.run(work_task, {})
        self.assertEqual(
            [record["id"] for record in work_result["output"]["results"]],
            ["search-work"],
        )
        for workspace in (None, "", "shared", True):
            input_values = {"query": "pdc"}
            if workspace is not None:
                input_values["workspace"] = workspace
            with self.subTest(workspace=workspace):
                with self.assertRaises(ValueError):
                    agent.run({"type": "memory.search", "input": input_values}, {})

    def test_real_search_agent_accepts_top_level_workspace_and_project(self):
        from agents.orchestrator import SearchAgent

        write_memory(
            self.root,
            id="search-project",
            title="PDC project",
            content="PDC project evidence",
            project="project-one",
        )
        write_memory(
            self.root,
            id="search-other-project",
            title="PDC other project",
            content="PDC other project evidence",
            project="project-two",
        )
        agent = SearchAgent(self.make_core())
        task = {
            "type": "memory.search",
            "workspace": "personal",
            "project": "project-one",
            "input": {"query": "pdc"},
        }

        result = agent.run(task, {})

        self.assertEqual(
            [record["id"] for record in result["output"]["results"]],
            ["search-project"],
        )

    def test_real_orchestrator_runs_import_with_top_level_workspace(self):
        import memory_tools
        from agents.orchestrator import ContextAgent, ImportAgent
        from agents.registry import AgentRegistry

        source = Path(self.temp.name) / "orchestrated-evidence.txt"
        source.write_text("raw evidence", encoding="utf-8")
        builder = RecordingBuilder()
        registry = AgentRegistry([ContextAgent(builder), ImportAgent(self.root, memory_tools)])
        kernel = Orchestrator(registry)
        task = {
            "type": "import.file",
            "workspace": "personal",
            "input": {"path": str(source)},
        }

        result = kernel.run(task)

        self.assertEqual(builder.calls, [("import.file", 8000, "personal", None)])
        self.assertTrue(result["output"]["written_paths"])

    def test_real_orchestrator_runs_legacy_import_without_workspace(self):
        import memory_tools
        from agents.orchestrator import ContextAgent, ImportAgent
        from agents.registry import AgentRegistry

        source = Path(self.temp.name) / "legacy-orchestrated-evidence.txt"
        source.write_text("raw evidence", encoding="utf-8")
        builder = RecordingBuilder()
        registry = AgentRegistry([ContextAgent(builder), ImportAgent(self.root, memory_tools)])
        kernel = Orchestrator(registry)
        task = {
            "type": "import.file",
            "input": {"path": str(source)},
        }

        result = kernel.run(task)

        self.assertEqual(builder.calls, [("empty", 8000)])
        self.assertTrue(result["output"]["written_paths"])

    def test_real_orchestrator_runs_review_with_top_level_workspace(self):
        from agents.orchestrator import ContextAgent, ReviewAgent
        from agents.registry import AgentRegistry

        builder = RecordingBuilder()
        gate = RecordingGate()
        registry = AgentRegistry([ContextAgent(builder), ReviewAgent(gate)])
        kernel = Orchestrator(registry)
        task = {
            "type": "memory.review",
            "workspace": "work",
            "input": {"action": "reject", "candidate_id": "candidate-1"},
        }

        result = kernel.run(task)

        self.assertEqual(builder.calls, [("memory.review", 8000, "work", None)])
        self.assertEqual(
            gate.calls,
            [("reject", "candidate-1", None, "work")],
        )
        self.assertEqual(result["output"], {"status": "reviewed"})

    def test_real_orchestrator_runs_legacy_review_without_workspace(self):
        from agents.orchestrator import ContextAgent, ReviewAgent
        from agents.registry import AgentRegistry

        builder = RecordingBuilder()
        gate = RecordingGate()
        registry = AgentRegistry([ContextAgent(builder), ReviewAgent(gate)])
        kernel = Orchestrator(registry)
        task = {
            "type": "memory.review",
            "input": {"action": "reject", "candidate_id": "candidate-1"},
        }

        result = kernel.run(task)

        self.assertEqual(builder.calls, [("empty", 8000)])
        self.assertEqual(
            gate.calls,
            [("reject", "candidate-1", None, None)],
        )
        self.assertEqual(result["output"], {"status": "reviewed"})

    def test_search_agent_validation_uses_immutable_workspace_snapshot(self):
        import memory
        from agents.orchestrator import SearchAgent

        original = list(memory.WORKSPACE_CHOICES)
        try:
            memory.WORKSPACE_CHOICES.append("shared")
            with self.assertRaises(ValueError):
                SearchAgent(self.make_core()).run(
                    {
                        "type": "memory.search",
                        "workspace": "shared",
                        "input": {"query": "pdc"},
                    },
                    {},
                )
        finally:
            memory.WORKSPACE_CHOICES[:] = original

    def test_real_import_agent_returns_envelope_and_never_creates_active_memory(self):
        import memory_tools
        from agents.orchestrator import ImportAgent
        from memory import collect_validated_records

        source = Path(self.temp.name) / "evidence.txt"
        source.write_text("raw evidence", encoding="utf-8")
        agent = ImportAgent(self.root, memory_tools)
        task = {"type": "import.file", "input": {"path": str(source)}}

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = agent.run(task, {})

        self.assertEqual(stdout.getvalue(), "")
        self.assertIs(validate_result(result, agent, task), result)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["output"]["written_paths"])
        self.assertTrue(
            all((self.root / path).exists() for path in result["output"]["written_paths"])
        )
        _, rows, errors, _ = collect_validated_records(self.root)
        self.assertEqual(errors, [])
        self.assertFalse(any(row["record"].get("status") == "active" for row in rows))
        self.assertEqual(agent.agent_id, "import_agent")
        self.assertEqual(agent.handles, ["import.file", "import.chatgpt"])

    def test_real_agents_and_future_adapters_match_registry(self):
        import memory_tools
        from agents.orchestrator import ContextAgent, ImportAgent, MemoryAgent, ReviewAgent, SearchAgent
        from agents.registry import AgentRegistry

        agents = [
            ImportAgent(self.root, memory_tools),
            MemoryAgent(self.make_core()),
            SearchAgent(self.make_core()),
            ReviewAgent(RecordingGate()),
            ContextAgent(RecordingBuilder()),
        ]
        registry = AgentRegistry.from_config(SOURCE_DIR / "agents/registry.yaml", agents)

        for agent in agents:
            for handle in agent.handles:
                self.assertIs(registry.select({"type": handle}), agent)

    def test_real_agents_reject_malformed_tasks_and_unsafe_keys(self):
        import memory_tools
        from agents.orchestrator import ImportAgent, MemoryAgent, SearchAgent

        malformed = [
            (ImportAgent(self.root, memory_tools), {"type": "import.file", "input": {"path": ""}}),
            (
                ImportAgent(self.root, memory_tools),
                {"type": "import.file", "input": {"path": "/tmp/x", "root": "/tmp"}},
            ),
            (MemoryAgent(self.make_core()), {"type": "memory.create", "input": []}),
            (
                SearchAgent(self.make_core()),
                {
                    "type": "memory.search",
                    "input": {"query": "PDC", "include_restricted": True},
                },
            ),
        ]
        for agent, task in malformed:
            with self.subTest(agent=agent.agent_id, task=task):
                with self.assertRaises(ValueError):
                    agent.run(task, {})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        import memory

        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "data"
        self.state = base / "state"
        memory.init_store(self.root)
        memory.db_init(argparse.Namespace(root=str(self.root), state_dir=str(self.state)))

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(SOURCE_DIR / "laos.py"),
                "--root",
                str(self.root),
                "--state-dir",
                str(self.state),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def run_task(self, task):
        return self.run_cli(
            "--task-json",
            json.dumps(task, ensure_ascii=False, separators=(",", ":")),
        )

    def test_build_application_loads_all_five_configured_agents(self):
        laos = importlib.import_module("laos")

        application = laos.build_application(self.root, self.state)

        self.assertIsInstance(application, Orchestrator)
        expected = {
            "import.file": "import_agent",
            "memory.create": "memory_agent",
            "memory.search": "search_agent",
            "memory.review": "review_agent",
            "context.build": "context_agent",
        }
        for task_type, agent_id in expected.items():
            with self.subTest(task_type=task_type):
                self.assertEqual(
                    application.registry.select({"type": task_type}).agent_id,
                    agent_id,
                )

    def test_cli_candidate_review_active_pipeline(self):
        from memory.store import MemoryStore

        create = self.run_task(
            {
                "type": "memory.create",
                "input": {
                    "type": "principle",
                    "title": "LAOS pipeline candidate",
                    "scope": "global",
                    "workspace": "personal",
                    "confidentiality": "personal",
                    "source": "manual:user_confirmed",
                    "confidence": "confirmed",
                    "content": "LAOS pipeline evidence",
                    "tags": ["laos"],
                },
            }
        )

        self.assertEqual(create.returncode, 0, create.stderr)
        created = json.loads(create.stdout)
        self.assertEqual(
            create.stdout,
            json.dumps(created, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
        )
        candidate_id = created["candidates"][0]
        self.assertEqual(MemoryStore(self.root).get(candidate_id)["status"], "candidate")

        review = self.run_task(
            {
                "type": "memory.review",
                "input": {"action": "accept", "candidate_id": candidate_id},
            }
        )

        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(MemoryStore(self.root).get(candidate_id)["status"], "active")

        search = self.run_task(
            {
                "type": "memory.search",
                "input": {"query": "pipeline evidence", "workspace": "personal"},
            }
        )
        context = self.run_task(
            {
                "type": "context.build",
                "input": {"query": "pipeline evidence", "workspace": "personal"},
            }
        )
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertEqual(
            [row["id"] for row in json.loads(search.stdout)["output"]["results"]],
            [candidate_id],
        )
        self.assertEqual(context.returncode, 0, context.stderr)
        self.assertEqual(json.loads(context.stdout)["output"]["sources"], [candidate_id])

    def test_cli_task_file_outputs_canonical_json(self):
        task_file = Path(self.temp.name) / "task.json"
        task_file.write_text(
            json.dumps(
                {
                    "type": "context.build",
                    "input": {"query": "nothing", "workspace": "personal"},
                }
            ),
            encoding="utf-8",
        )

        result = self.run_cli("--task-file", str(task_file))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
        )

    def test_cli_task_file_accepts_utf8_bom(self):
        task_file = Path(self.temp.name) / "bom-task.json"
        task_file.write_text(
            json.dumps(
                {
                    "type": "context.build",
                    "input": {"query": "nothing", "workspace": "personal"},
                }
            ),
            encoding="utf-8-sig",
        )

        result = self.run_cli("--task-file", str(task_file))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["output"]["sources"], [])

    def test_cli_failures_are_one_safe_json_line_and_nonzero(self):
        cases = [
            ("--task-json", json.dumps({"type": "unknown", "input": {}})),
            ("--task-json", "not-json"),
            (
                "--task-json",
                json.dumps({"type": "context.build", "input": {}}),
                "--task-file",
                str(Path(self.temp.name) / "missing.json"),
            ),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertNotEqual(result.returncode, 0)
                error = json.loads(result.stderr)
                self.assertEqual(
                    error,
                    {
                        "error": {
                            "code": "request_failed",
                            "message": "Request failed.",
                        }
                    },
                )
                self.assertEqual(result.stderr.count("\n"), 1)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(str(self.root), result.stderr)
                self.assertNotIn(str(self.state), result.stderr)


if __name__ == "__main__":
    unittest.main()
