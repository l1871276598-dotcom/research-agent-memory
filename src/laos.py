import argparse
import json
import sys
from pathlib import Path

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


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def build_application(root, state_dir=None, model_backend=None):
    store = MemoryStore(root)
    candidates = CandidateStore(root, state_dir)
    core = MemoryCore(store, candidates)

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
    except Exception:
        _write_json(
            sys.stderr,
            {"error": {"code": "request_failed", "message": "Request failed."}},
        )
        return 1
    _write_json(sys.stdout, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
