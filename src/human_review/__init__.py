"""Human Review Phase — human decision layer over Phase 5 review queue.

decision.py  : pure functions (no I/O) — schema, canonicalizer, digest,
               recorded_at lexer, §5 layered decoder, state reduction.
review_store.py : all I/O (added in HR.2).

Frozen contracts: Human Review design baseline v1.8, Phase 5 baseline v4.3.2.
"""
