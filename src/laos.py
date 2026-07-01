import argparse
import json
import sys
from pathlib import Path

import memory_tools
from agents.orchestrator import ContextAgent, ImportAgent, MemoryAgent, ReviewAgent, SearchAgent
from agents.reflection import ConversationReviewAgent
from agents.reflection_record import ReflectionRecordAgent
from agents.registry import AgentRegistry
from context.builder import ContextBuilder
from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore
from orchestrator import Orchestrator
from reflection import ConversationReviewCoordinator, ConversationReviewService, ReviewStateStore
from review.gate import ReviewGate
from runtime.codex import CodexConversationReviewer


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def build_application(root, state_dir=None):
    store = MemoryStore(root)
    candidates = CandidateStore(root, state_dir)
    core = MemoryCore(store, candidates)
    review_service = ConversationReviewService(core, CodexConversationReviewer())
    review_coordinator = ConversationReviewCoordinator(
        review_service,
        ReviewStateStore(candidates.state_dir),
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
        ConversationReviewAgent(review_service),
        ReflectionRecordAgent(review_coordinator),
    ]
    config = Path(__file__).with_name("agents") / "runtime_registry.yaml"
    return Orchestrator(AgentRegistry.from_config(config, agents))


def _parser():
    parser = JsonArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-dir")
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
        result = build_application(args.root, args.state_dir).run(task)
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
