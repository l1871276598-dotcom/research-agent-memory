# SQLite v2 → v3 compatible-superset migration

`memory.py migrate-db-v2-v3` never migrates in place. It validates a read-only v2 source, copies it with the SQLite backup API, preserves all legacy tables, columns, indexes and rows, then adds only `documents`, `document_fts`, `document_index_state` and their required indexes.

Compatibility requires schema version 3, every required table and compatible required column type, working FTS5 virtual tables, and required indexes or constraints. Extra compatible tables, columns and old indexes are allowed; missing columns, wrong affinities and non-FTS replacements are rejected.

The candidate is checkpointed and checked with `integrity_check`, `quick_check`, file hashes, source hashes, FTS counts and path existence before activation. Source and output must differ. A failed migration removes its temporary output. Production activation requires an external recoverable backup and atomic replacement.

Markdown and raw files remain authoritative. SQLite remains a local, rebuildable index.
