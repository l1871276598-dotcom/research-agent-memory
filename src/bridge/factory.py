from __future__ import annotations

from memory.candidate import CandidateStore
from memory.core import MemoryCore
from memory.store import MemoryStore
from procedures.proposals import ProcedureProposalStore
from reflection import ConversationReviewCoordinator, ConversationReviewService, ReviewStateStore
from sessions import SessionStore

from .inbox import BridgeEventInbox
from .projector import BridgeScopeResolver, SessionProjector
from .service import BridgeIngestService, BridgePipeline


def build_bridge_pipeline(
    data_root,
    state_dir,
    *,
    token,
    model_backend=None,
    scope_mappings=None,
    default_scope=None,
    allowed_sources=None,
    memory_interval=10,
    skill_interval=10,
    max_payload_bytes=1_000_000,
):
    """Build a bridge pipeline.

    Passing no model backend keeps capture and session projection fully local.
    Supplying a backend with ``review(messages)`` enables automatic candidate
    extraction without changing the bridge ingestion path.
    """
    inbox = BridgeEventInbox(state_dir)
    sessions = SessionStore(state_dir)
    coordinator = None
    if model_backend is not None:
        reviewer = getattr(model_backend, "review", None)
        if not callable(reviewer):
            raise ValueError("model backend does not support conversation review")
        store = MemoryStore(data_root)
        candidates = CandidateStore(data_root, state_dir)
        core = MemoryCore(store, candidates)
        review_service = ConversationReviewService(core, reviewer)
        coordinator = ConversationReviewCoordinator(
            review_service,
            ReviewStateStore(candidates.state_dir),
            procedure_proposals=ProcedureProposalStore(candidates.state_dir),
            memory_interval=memory_interval,
            skill_interval=skill_interval,
        )
    resolver = BridgeScopeResolver(scope_mappings, default_scope)
    projector = SessionProjector(
        inbox,
        sessions,
        coordinator=coordinator,
        scope_resolver=resolver,
    )
    service = BridgeIngestService(
        inbox,
        token=token,
        allowed_sources=allowed_sources,
        max_payload_bytes=max_payload_bytes,
    )
    pipeline = BridgePipeline(service, projector)
    pipeline.inbox = inbox
    pipeline.sessions = sessions
    pipeline.coordinator = coordinator
    return pipeline
