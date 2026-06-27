# Trusted Memory Loop v0.7.0

The existing `memory_distill.py` implements the loop without an Agent framework or a new SQLite run table.

Flow: READ → PROPOSE → VALIDATE → CONFLICT CHECK → FEEDBACK → APPLY → REINDEX → VERIFY → STOP/RESUME.

Deterministic actions are `ADD`, `UPDATE`, `DEPRECATE`, `NOOP` and `REVIEW_REQUIRED`. Exact content, source-hash and repeated-candidate matches may become `NOOP`. Same-title/different-content and other semantic ambiguity become `REVIEW_REQUIRED`; semantic conflicts are never merged automatically. Legacy review action names remain CLI-compatible aliases.

All creation commands and functions produce candidates. `--confirmed` is retained only as explicit confirmation metadata; it neither activates a record nor rewrites provenance. Active records are produced only by the common review/accept path. This release does not authenticate the review/accept operator; owner/agent authentication remains unimplemented. Explicit replacement deprecates the old record, activates the new record and records both supersession directions; old records are retained.

UPDATE and DEPRECATE proposals snapshot the target ID, status and file SHA256. Accept compares that snapshot with the current store, builds and validates the complete hypothetical store, and returns structured `stale_target` feedback before any file or index write when the target changed. A context transition is one candidate; accept atomically deprecates the old context, creates the active new context and archives the accepted transition.

Every run transition appends and fsyncs one JSON object to `<data-root>/state/distill_runs.jsonl`. Entries contain run ID, iteration (maximum two), input hash, proposal IDs, structured errors, stop reason, next action, resume command and timestamp. Resume reads the last valid event and never re-applies completed work. A malformed line is reported without rewriting history.

Accept applies files atomically through the existing replacement primitive, calls the existing `index_store()`, verifies SQLite hashes and FTS rows, and restores files plus reindexes on failure. ChatGPT and manual raw imports use exclusive creation and stable hash-version paths; existing raw files are never overwritten.

Not implemented: automatic semantic resolution, MCP, owner/agent authentication, vector search, GUI or a large coordinator.
