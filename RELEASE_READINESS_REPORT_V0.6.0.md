# Research Agent Memory v0.6.0 Release Readiness Report

## Baseline

- Start branch: `feat/v0.6.0-hybrid-search`
- Start commit: `5e6d880 feat(search): add optional hybrid retrieval`
- Python used for final verification: `/opt/homebrew/bin/python3.13` (`Python 3.13.14`)
- SQLite schema: `v3`
- Default retrieval mode: `lexical`
- Final release-fix commit: included in the completion response after commit creation
- Final worktree status at report generation: modified files awaiting the release-fix commit

## Issues Found And Root Causes

1. README overstated or blurred v0.6.0 capability boundaries.
   - Root cause: README still mixed planned lifecycle/semantic wording with the actual CLI.
   - Fix: rewrote README around the real v0.6.0 commands and boundaries.

2. Candidate review mixed memory lifecycle state with audit state.
   - Root cause: `accepted` and `rejected` were stored as `status` values.
   - Fix: added `audit_status`; kept `status` for memory state only.

3. Manual raw/text indexing could duplicate the same evidence and index binary raw incorrectly.
   - Root cause: document collection scanned `imports/manual/raw` and `imports/manual/text` equally.
   - Fix: manual text sidecars now win; raw is indexed only when no sidecar exists and the raw file is safe UTF-8 text.

4. Manual import claimed direct support for binary formats without extraction.
   - Root cause: PDF/DOCX were listed as text suffixes but read as UTF-8.
   - Fix: PDF/DOCX are archived raw-only with `archived_without_text: 1`.

5. ChatGPT `--restore-recent` was exposed without a real recent restore pipeline.
   - Root cause: parser exposed a future-facing flag that only changed reporting.
   - Fix: removed the CLI flag and report that import-chatgpt does not restore recent in v0.6.0.

6. `document-meta set` after indexing did not refresh project/workspace filters.
   - Root cause: document index freshness considered only source file SHA, not metadata overrides.
   - Fix: document index state now hashes source SHA plus `metadata_json`; `documents.sha256` remains the raw/source SHA.

7. Search mode fallback was not explicit enough in JSON output.
   - Root cause: JSON search output was a bare list, so empty results could not show requested/effective mode.
   - Fix: JSON search output now includes `requested_mode`, `effective_mode`, `warnings`, and `results`.

8. CI did not run the full documented release gate.
   - Root cause: workflow omitted `git diff --check` and used non-quiet compileall.
   - Fix: workflow now runs tests, `compileall -q src`, and `git diff --check`.

9. `.gitignore` missed release-sensitive generated artifacts.
   - Root cause: ZIP/log/distillation patterns were absent.
   - Fix: added ZIP, log, distillation, and local state ignore patterns.

## Code Fixes

- `src/memory.py`
  - Added `AUDIT_STATUS_CHOICES`.
  - Added `audit_status` validation and rendering.
  - Added manual raw/text de-duplication and binary raw skip logic.
  - Added source-tracing front matter parsing for manual sidecars.
  - Added explicit search mode summaries.
  - Wrapped JSON search output with mode metadata.
  - Included document metadata in index freshness.

- `src/memory_distill.py`
  - Writes `audit_status: awaiting_review` on candidate creation.
  - Marks accepted create/supersede records as `status: active`, `audit_status: accepted`.
  - Marks merge/support/reject records as `status: archived` with the corresponding audit status.
  - Marks conflict records as `status: conflict`, `audit_status: conflict`.

- `src/memory_tools.py`
  - Removed unsupported `--restore-recent` CLI exposure.
  - Added raw-only archive behavior for PDF/DOCX and unreadable text.
  - Added source-tracing metadata to manual text sidecars.

- `.github/workflows/ci.yml`
  - Added `git diff --check`.
  - Uses `python3 -m compileall -q src`.

- `.gitignore`
  - Added generated ZIP/log/distillation/local state patterns.

## README Fixes

- Added current version status, schema, default retrieval mode, and platform/Python requirements.
- Documented exact CLI commands and removed unsupported examples.
- Documented actual candidate review flow: `apply -> review -> accept/reject`.
- Explicitly states `prepare`, `run`, `purge`, and `status` are not current `memory_distill.py` commands.
- Clarified manual `--path` only; no directory/inbox scan.
- Clarified manual text sidecar priority and binary raw non-indexing.
- Clarified ChatGPT ZIP manual-only import and import report paths.
- Clarified semantic/hybrid are mode interfaces with explicit lexical fallback, not real vector search.
- Added backup/recovery, CI, confidentiality, and v0.6.0 exclusions.
- Explicitly notes iCloud multi-device sync, device state, automatic index refresh, and mobile enhancements are not v0.6.0 and were not implemented.

## Tests

- Test files changed:
  - `tests/test_memory.py`
  - `tests/test_memory_distill.py`
  - `tests/test_memory_tools.py`

- Tests added or expanded:
  - Candidate `audit_status` separation.
  - Candidate conflict review behavior.
  - Manual text sidecar wins over raw.
  - Binary raw is not indexed.
  - Manual import archives PDF/DOCX raw-only.
  - Document metadata changes are reindexed.
  - Search JSON requested/effective mode fields.
  - Removal of unsupported `--restore-recent`.

- Test count change:
  - Baseline observed during this task: 138 tests.
  - Final count: 142 tests.
  - Explanation: four focused regression tests were added for release-critical candidate, manual import/indexing, and metadata reindex behavior. No test files were deleted.

## CLI Help Audit

Existing commands verified with `--help`:

- `src/memory.py`: `init`, `add`, `validate`, `export`, `context-transition`, `db-init`, `index`, `db-rebuild`, `search`, `doctor`, `document-meta`, `document-meta set`, `document-meta unset`, `project-status`, `context`, `evaluate-search`.
- `src/memory_tools.py`: `import-chatgpt`, `import-manual`.
- `src/memory_distill.py`: `apply`, `review`, `accept`, `reject`.

Nonexistent by design and documented as absent:

- `src/memory_distill.py prepare`
- `src/memory_distill.py run`
- `src/memory_distill.py purge`
- `src/memory_distill.py status`

## Smoke Test Result

Temp-only smoke test result: `SMOKE_OK`.

Covered:

- `init`, `db-init`, `validate`, `index`, `doctor`.
- Adding principle, project, project-scoped decision, and procedure.
- Search project and workspace filters.
- Dynamically generated ChatGPT ZIP dry-run, first import, repeat import, reports, and raw-only reporting.
- Manual TXT/Markdown/JSON/CSV import.
- PDF raw-only import with `archived_without_text`.
- Manual text indexing and no raw duplicate results.
- `document-meta set`, project search, `unset`, and re-index.
- Candidate create/review/accept/reject/conflict.
- Context Pack JSON and Markdown.
- lexical, semantic, and hybrid requested/effective mode reporting.
- Retrieval evaluation with hybrid fallback.

## Capability State

- SQLite schema: `v3`.
- Default retrieval mode: `lexical`.
- Real semantic retrieval: not implemented.
- Real embeddings/chunks/vector tables/cosine similarity/RRF: not implemented.
- `semantic` / `hybrid`: accepted as requested modes, explicitly fall back to `lexical` with warnings.
- ChatGPT ZIP import: local official ZIP only; no email monitoring, auto-download, browser login, or network redirect handling.
- Manual raw/text indexing: text sidecar is primary; safe UTF-8 raw is fallback only; binary raw is not indexed.
- Candidate state machine: memory `status` is separate from `audit_status`.
- Context Pack: implemented with stale-index checks, project isolation, source tracing, and budget handling.
- GitHub Actions: configured for push and pull request; remote CI has not been run in this task.

## Safety Scan

Commands run:

- `git ls-files`
- tracked artifact scan for SQLite, ZIP, PDF, `.env`, private keys, and memory JSONL
- keyword scan for Gmail, approve-host, Bearer, auto ZIP download, ChatGPT auto login, sync commands, device commands, and cloud/vector database terms

Results:

- No tracked `.sqlite`, `.sqlite3`, `.sqlite-wal`, `.sqlite-shm`, `.zip`, `.pdf`, `.env`, private key, or `memory.jsonl` files found.
- No Gmail monitor, approve-host, Bearer-token service, auto ZIP download, ChatGPT auto-login, or sync command code found.
- Remaining keyword hits are either README safety exclusions, tests for removed commands, tokenization variable names, or historical roadmap documentation outside the v0.6.0 README.

## Final Verification

Fresh verification commands executed:

```bash
/opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
/opt/homebrew/bin/python3.13 -m compileall -q src
git diff --check
/opt/homebrew/bin/python3.13 -m json.tool schemas/memory.schema.json >/dev/null
```

Results:

- Unit tests: `Ran 142 tests` / `OK`.
- `compileall`: passed.
- `git diff --check`: passed.
- Schema JSON parse: passed.

## Unfinished Items

- Remote GitHub Actions were not run because the branch was not pushed.
- No push, PR, merge, or tag was created, by request.
- v0.6.0 does not implement iCloud multi-device sync, device state, automatic index refresh, mobile enhancements, automatic candidate generation, real vector semantic search, MCP, or a total-control Agent.

## Draft PR Readiness

Based on local evidence, the branch is ready for a Draft PR after the release-fix commit is created and pushed. Do not claim remote CI has passed until GitHub Actions actually run on the pushed branch.
