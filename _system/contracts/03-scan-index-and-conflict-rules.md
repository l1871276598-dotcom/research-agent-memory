# Phase 0 Contract — Scan, Index & Conflict Rules

**Version**: 0.1.0
**Status**: frozen
**Frozen**: 2026-07-07

---

## 1. Scan Flow

```
For each file in vault (excluding _system/, .obsidian/, .DS_Store):

  1. Skip .icloud placeholder files → log, continue
  2. Read mtime + size → compare with last indexed record
  3. If mtime+size unchanged → skip
  4. Wait for file stability (1s cooldown after last mtime change)
  5. Read full content as UTF-8
  6. Compute evidence_hash (full file, minimal normalization: UTF-8, LF only)
  7. Compare evidence_hash with last_indexed_hash
  8. If identical → update mtime/size in manifest, skip re-index
  9. Parse frontmatter (YAML) + body + [[wikilinks]]
  10. Validate frontmatter against schema
  11. If valid → update FTS5 index, write manifest receipt
  12. If invalid YAML → quarantine, retain last valid version
```

## 2. Hash Definitions

### evidence_hash
- **Purpose**: index consistency, Review Gate, receipt, tamper detection
- **Normalization**: UTF-8, LF line endings only
- **Scope**: entire file content
- **Does NOT exclude**: `updated`, trailing whitespace, any frontmatter field

### rename_fingerprint
- **Purpose**: heuristic rename detection for notes without ULID
- **Normalization**: UTF-8, LF, strip trailing whitespace per line
- **Excludes from hash input**: `updated` field value
- **Match behavior**: fingerprint match + different path → possible rename. Uncertain → fail-closed (tombstone old + index new, flag for human)

## 3. Index Manifest Table (SQLite)

```sql
CREATE TABLE index_manifest (
    note_id        TEXT PRIMARY KEY,   -- ULID or content_hash fallback
    relative_path  TEXT NOT NULL,
    mtime          INTEGER,
    size           INTEGER,
    evidence_hash  TEXT NOT NULL,
    last_indexed_hash TEXT,
    rename_fingerprint TEXT,
    parser_version TEXT,
    schema_version INTEGER,
    indexed_at     TEXT,              -- ISO timestamp
    status         TEXT               -- active | tombstone | quarantined
);
```

## 4. Delete Detection

```
On each full scan:
  1. List all files currently in vault
  2. Compare with all active paths in index_manifest
  3. Missing files → set status = 'tombstone'
  4. Keep tombstone records (do not delete from DB)
  5. Tombstone notes excluded from search results
  6. Audit log records: {note_id, path, tombstoned_at, last_evidence_hash}
```

## 5. Rename Detection

```
If file at new_path has:
  - ULID matching existing record → update path, same note
  - No ULID, rename_fingerprint matches exactly one tombstone → same note, update path
  - No ULID, rename_fingerprint matches multiple → fail-closed: index as new, flag for review
  - No ULID, no fingerprint match → index as new note
```

## 6. Quarantine Rules

Triggers for quarantine:
- YAML parse failure (invalid frontmatter)
- schema_version > parser supports
- Required field missing (`type`, `created`)
- File encoding not valid UTF-8

Quarantined notes:
- Retain last valid version in index
- Excluded from search results
- Logged with reason and timestamp
- Human must fix and re-scan

## 7. LAOS Self-Write Detection

LAOS-generated files carry `source: laos` and `run_id` in frontmatter.
Scanner checks:
- If `source: laos` and `run_id` matches current run → skip (own output)
- If `source: laos` and `run_id` differs → process normally (previous run output)

## 8. Atomic Write Protocol

When LAOS writes to vault:
1. Write content to temp file: `{target}.laos-tmp-{run_id}`
2. Verify temp file content_hash matches expected
3. `mv` temp to target (atomic on same filesystem)
4. Update index_manifest
5. On failure: remove temp file, log error, do NOT touch target

## 9. Conflict Detection (Promotion)

Before Review Gate promotion to formal directory:
1. Check if target path exists
2. If exists, compare evidence_hash with hash at time of candidate generation
3. If hash changed → human modified file since candidate was created → abort promotion, log conflict, flag for human
4. If hash unchanged → safe to promote (atomic write)
