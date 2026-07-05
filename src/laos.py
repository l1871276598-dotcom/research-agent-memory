import argparse
import json
import sys
from pathlib import Path

import memory
import memory_tools
from agents.candidate_generator import LowRiskCandidateAgent
from agents.coordinator import LoopCoordinatorAgent
from agents.orchestrator import ContextAgent, ImportAgent, MemoryAgent, ReviewAgent, SearchAgent
from agents.policy import PolicyAgent
from agents.reflection import ConversationReviewAgent, ReflectionAgent
from agents.reflection_record import ReflectionRecordAgent
from agents.registry import AgentRegistry
from context.builder import ContextBuilder
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore
from models import build_model_backend
from orchestrator import Orchestrator
from procedures.proposals import ProcedureProposalStore
from reflection import ConversationReviewCoordinator, ConversationReviewService, ReviewStateStore
from review.gate import ReviewGate


_INVALID_MEMORY_ROOT_MESSAGE = "请先执行：python3 src/memory.py init --root PATH"


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def build_application(root, state_dir=None, model_backend=None):
    store = MemoryStore(root)
    candidates = CandidateStore(root, state_dir)

    def search_documents(query, workspace, project):
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
        ReviewAgent(
            ReviewGate(root, candidates.state_dir),
            default_workspace="personal",
        ),
        ContextAgent(ContextBuilder(store)),
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
    message = str(error)
    if message == _INVALID_MEMORY_ROOT_MESSAGE:
        return "invalid_memory_root"
    if message == "memory store validation failed":
        return "memory_store_validation_failed"
    return "request_failed"


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        text = args.task_json
        if text is None:
            text = Path(args.task_file).read_text(encoding="utf-8-sig")
        task = json.loads(text)
        backend = None
        if args.model_config:
            model_config = json.loads(
                Path(args.model_config).read_text(encoding="utf-8-sig")
            )
            backend = build_model_backend(model_config)
        result = build_application(args.root, args.state_dir, backend).run(task)
    except Exception as error:
        _write_json(
            sys.stderr,
            {
                "error": {
                    "code": _request_error_code(error),
                    "message": "Request failed.",
                }
            },
        )
        return 1
    _write_json(sys.stdout, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
