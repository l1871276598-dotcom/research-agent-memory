import argparse
import importlib
import importlib.machinery
import json
import os
import sys
import types
from pathlib import Path

import memory
import memory_agent
import memory_tools
from agents.candidate_generator import LowRiskCandidateAgent
from agents.coordinator import LoopCoordinatorAgent
from agents.evidence import EvidenceAgent
from agents.handoff import HandoffAgent
from agents.orchestrator import (
    ActivationAgent,
    CandidateListAgent,
    ContextAgent,
    ImportAgent,
    MemoryAgent,
    ReviewAgent,
    SearchAgent,
)
from agents.policy import PolicyAgent
from agents.reflection import ConversationReviewAgent, ReflectionAgent
from agents.reflection_record import ReflectionRecordAgent
from agents.registry import AgentRegistry
from agents.vault_read import VaultReadAgent
from context.builder import ContextBuilder
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore
from models import build_model_backend
from orchestrator import Orchestrator
from procedures.proposals import ProcedureProposalStore
from reflection import ConversationReviewCoordinator, ConversationReviewService, ReviewStateStore
from review import AuthorityMemoryStore, AuthorityStore, ReviewGate


_INVALID_MEMORY_ROOT_MESSAGE = "\u8bf7\u5148\u6267\u884c\uff1apython3 src/memory.py init --root PATH"
_REVIEW_TASKS = frozenset({"review.list", "review.show", "review.decide"})


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def build_application(root, state_dir=None, model_backend=None):
    base_store = MemoryStore(root)
    candidates = CandidateStore(root, state_dir)
    authority = AuthorityStore(root, candidates.state_dir)
    store = AuthorityMemoryStore(base_store, authority)

    def search_documents(query, workspace, project, confidentiality=None):
        return memory.search_store(
            argparse.Namespace(
                root=str(root),
                state_dir=str(candidates.state_dir),
                query=query,
                kind="document",
                source_kind=None,
                project=project,
                context_id=None,
                workspace=workspace,
                status=None,
                as_of=None,
                include_unassigned=False,
                include_restricted=False,
                include_inactive=False,
                confidentiality=confidentiality,
                limit=20,
                json=True,
                mode="lexical",
            )
        )

    core = MemoryCore(store, candidates, search_documents)

    reflection = ReflectionAgent(candidates.state_dir)
    policy = PolicyAgent(root, candidates.state_dir)
    generator = LowRiskCandidateAgent(core, candidates.state_dir)

    backend = model_backend or build_model_backend()
    if not callable(getattr(backend, "review", None)):
        raise ValueError("model backend does not support conversation review")
    review_service = ConversationReviewService(core, backend.review)
    review_coordinator = ConversationReviewCoordinator(
        review_service,
        ReviewStateStore(candidates.state_dir),
        procedure_proposals=ProcedureProposalStore(candidates.state_dir),
    )

    agents = [
        ImportAgent(root, memory_tools),
        MemoryAgent(core),
        SearchAgent(core),
        CandidateListAgent(core),
        EvidenceAgent(candidates.state_dir),
        # GP4-01: the vault root is a Core-side authority from administrator
        # configuration (LAOS_VAULT_ROOT). A caller can never supply it.
        VaultReadAgent(os.environ.get("LAOS_VAULT_ROOT") or ""),
        ReviewAgent(
            ReviewGate(
                root,
                candidates.state_dir,
                authority_store=authority,
            )
        ),
        ActivationAgent(
            ReviewGate(
                root,
                candidates.state_dir,
                authority_store=authority,
            )
        ),
        ContextAgent(ContextBuilder(store)),
        HandoffAgent(root),
        reflection,
        policy,
        generator,
        LoopCoordinatorAgent(
            root,
            candidates.state_dir,
            reflection,
            policy,
            generator,
        ),
        ConversationReviewAgent(review_service),
        ReflectionRecordAgent(review_coordinator),
    ]
    config = Path(__file__).with_name("agents") / "registry-v0.9.yaml"
    return Orchestrator(AgentRegistry.from_config(config, agents))


def _normalized_path(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _module_paths(module):
    try:
        return {_normalized_path(path) for path in module.__path__}
    except (AttributeError, TypeError) as exc:
        raise ValueError("untrusted src namespace") from exc


def _pin_and_load_rq_builder():
    repo_src = Path(__file__).resolve().parent
    expected_src = _normalized_path(repo_src)
    expected_loop = _normalized_path(repo_src / "learning_loop" / "__init__.py")
    expected_loop_path = _normalized_path(repo_src / "learning_loop")
    expected_queue = _normalized_path(
        repo_src / "learning_loop" / "queue_request.py")

    src_module = sys.modules.get("src")
    if src_module is None:
        src_module = types.ModuleType("src")
        src_module.__package__ = "src"
        src_module.__path__ = [str(repo_src)]
        spec = importlib.machinery.ModuleSpec(
            "src", loader=None, is_package=True)
        spec.submodule_search_locations = [str(repo_src)]
        src_module.__spec__ = spec
        sys.modules["src"] = src_module
    elif _module_paths(src_module) != {expected_src}:
        raise ValueError("untrusted src namespace")

    loop_module = sys.modules.get("src.learning_loop")
    if loop_module is None:
        loop_module = importlib.import_module("src.learning_loop")
    elif (
        _normalized_path(getattr(loop_module, "__file__", ""))
        != expected_loop
        or _module_paths(loop_module) != {expected_loop_path}
    ):
        raise ValueError("untrusted learning_loop package")

    queue_module = sys.modules.get("src.learning_loop.queue_request")
    if queue_module is None:
        queue_module = importlib.import_module(
            "src.learning_loop.queue_request")
    elif _normalized_path(getattr(queue_module, "__file__", "")) \
            != expected_queue:
        raise ValueError("untrusted queue_request module")

    builder = getattr(queue_module, "build_review_queue_request", None)
    if (
        _module_paths(src_module) != {expected_src}
        or _normalized_path(getattr(loop_module, "__file__", ""))
        != expected_loop
        or _module_paths(loop_module) != {expected_loop_path}
        or _normalized_path(getattr(queue_module, "__file__", ""))
        != expected_queue
        or not callable(builder)
        or builder.__module__ != "src.learning_loop.queue_request"
    ):
        raise ValueError("invalid review queue builder provenance")
    return builder


def build_review_transport(state_dir, forbidden_roots, *, rq_builder):
    from human_review.review_store import ReviewStore
    from agents.human_review import HumanReviewAgent

    store = ReviewStore(
        state_dir,
        forbidden_roots=forbidden_roots,
        rq_builder=rq_builder,
    )
    agent = HumanReviewAgent(store)
    config = Path(__file__).with_name("agents") / "registry-review.yaml"
    return AgentRegistry.from_config(config, [agent])


def _parser():
    parser = JsonArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--model-config")
    tasks = parser.add_mutually_exclusive_group(required=True)
    tasks.add_argument("--task-json")
    tasks.add_argument("--task-file")
    return parser


def _write_json(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    stream.write("\n")


def _request_error_code(error):
    if isinstance(error, memory_agent.AgentError):
        return error.code
    message = str(error)
    if message == _INVALID_MEMORY_ROOT_MESSAGE:
        return "invalid_memory_root"
    if message == "memory store validation failed":
        return "memory_store_validation_failed"
    return "request_failed"


def _error_stage(error):
    message = str(error)
    if "handoff" in message.lower():
        return "handoff.write"
    if "memory" in message.lower():
        return "memory"
    if "search" in message.lower():
        return "memory.search"
    return "task"


def _review_error(error):
    value = {"category": error.category, "code": error.code}
    if error.rq_id is not None:
        value["rq_id"] = error.rq_id
    if error.decision_seq is not None:
        value["decision_seq"] = error.decision_seq
    return {"error": value}


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        text = args.task_json
        if text is None:
            text = Path(args.task_file).read_text(encoding="utf-8-sig")
        task = json.loads(text)
        if isinstance(task, dict) and task.get("type") in _REVIEW_TASKS:
            if args.state_dir is None:
                raise ValueError("--state-dir is required for review tasks")
            rq_builder = _pin_and_load_rq_builder()
            from human_review.review_store import ReviewError
            transport = build_review_transport(
                args.state_dir,
                (args.root,),
                rq_builder=rq_builder,
            )
            try:
                result = transport.select(task).run(task, {})
            except ReviewError as error:
                _write_json(sys.stderr, _review_error(error))
                return 1
        else:
            backend = None
            if args.model_config:
                model_config = json.loads(
                    Path(args.model_config).read_text(encoding="utf-8-sig")
                )
                backend = build_model_backend(model_config)
            result = build_application(
                args.root, args.state_dir, backend).run(task)
    except Exception as error:
        _write_json(
            sys.stderr,
            {
                "error": {
                    "code": _request_error_code(error),
                    "message": str(error),
                    "stage": _error_stage(error),
                }
            },
        )
        return 1
    _write_json(sys.stdout, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
