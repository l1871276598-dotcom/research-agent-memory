# Stage 17 — macOS Final Integration Review

Date: 2026-07-05
Decision: PASS for local macOS integration
Release target: v0.9.0
Current repository metadata: v0.9.0-development
Current platform scope: macOS only
Current branch: feat/laos-memory-search-tool
Reviewed HEAD: 716a11b98ee6b658039096c53b14aca4d1223811

## Review purpose

Stage 17 closes the documentation and integration gap after the earlier Stage 16 review. It covers the current HEAD additions that were not present in the Stage 16 evidence and verifies them against the active macOS-first delivery decision.

This review authorizes only the next remote-review preparation step. It does not push the branch, create or update a Draft PR, claim remote CI success, merge, tag, publish a GitHub Release, or deploy a service.

## Incremental scope after Stage 16

The review covers the following current-branch additions:

- `5601378` — stable LAOS memory-root error classification
- `217c81a` — Agent document search integration
- `fc90ab1` — read-only LAOS memory search tool
- `e8279e5` — bounded memory search result limits
- `ad1c553` — checkpoint continuity acceptance
- `9f07c53` — macOS-first delivery scope and macOS CI
- `fad8852` — explicit SQLite health-check connection closure
- `716a11b` — fixed-scope Developer Bridge Adapter and regression coverage
- current documentation alignment in README, ROADMAP, PHASE_STATUS, and this review record

## Platform decision

The active implementation, integration, acceptance, and release scope is macOS with Python 3.11 or later. Linux and Windows compatibility are deferred until the macOS feature set and release gates are complete.

This means:

- macOS failures block current delivery;
- Linux-only and Windows-only compatibility work does not block current delivery;
- current documentation must not claim active Linux or Windows support;
- the CI workflow uses `macos-latest` as the current required platform;
- later cross-platform work must have its own compatibility matrix and acceptance evidence.

The controlling decision is `docs/decisions/2026-07-05-macos-first-delivery-scope.md`.

## Functional review

### Memory and document search

The current runtime exposes read-only, bounded search through the native LAOS application and tool facade. Tests verify:

- workspace and project scoping;
- restricted-content exclusion;
- bounded result limits;
- document and memory result integration;
- no modification of authoritative memory during search;
- safe rejection of malformed application output and invalid roots.

### Checkpoint continuity

Checkpoint acceptance verifies that a captured exchange is recoverable from a fresh facade and that retrying the same checkpoint with identical content is idempotent.

A dedicated regression verifies that reusing a final checkpoint ID with changed content fails closed. The original two messages remain unchanged and no partial replacement is written.

### Developer Bridge Adapter

`tools/developer_bridge_adapter.py` exposes exactly three operations:

- `capture_checkpoint`
- `session_search`
- `session_get`

The adapter is constrained by fixed environment configuration and rejects:

- arbitrary command, path, argument, or environment injection;
- missing, partial, relative, noncanonical, symlinked, overlapping, temporary, or synchronized state paths;
- malformed or symlinked data-root markers;
- cross-workspace or cross-project reads;
- malformed, oversized, non-finite, or out-of-contract input and output;
- changed content for an immutable final checkpoint.

The adapter suppresses downstream stdout and stderr, returns one compact JSON line, uses bounded input and response sizes, and maps failures to safe error codes.

### SQLite connection closure

The health-check probes now wrap SQLite connections with `contextlib.closing`. Dedicated tests verify that both the in-memory FTS5 probe and file database probe call `close()` exactly once.

The final integrated run emitted no SQLite `ResourceWarning`.

## Safety and compatibility review

The current increments do not change the following invariants:

1. Agent-created memory remains candidate-only.
2. Only Review Gate actions can create active memory.
3. Workspace, project, confidentiality, and restricted-content boundaries remain enforced.
4. No automatic policy approval, candidate activation, background retry, or autonomous task execution was introduced.
5. Markdown / JSONL remain authoritative; SQLite remains local and rebuildable.
6. The Developer Bridge Adapter does not expose arbitrary shell execution or caller-selected filesystem roots.
7. MCP checkpoint remains an explicit tool channel and is not described as passive browser capture.
8. Linux and Windows are not claimed as current supported release platforms.

## Documentation alignment

The following files now agree on current scope and gates:

- `README.md`
- `docs/ROADMAP.md`
- `docs/PHASE_STATUS.json`
- `docs/decisions/2026-07-05-macos-first-delivery-scope.md`
- this Stage 17 review

The documentation distinguishes:

```text
local macOS integration
→ branch commit
→ branch push
→ Draft PR macOS CI
→ human review
→ merge
→ tag
→ GitHub Release
```

No earlier gate is presented as proof that a later gate passed.

## Verification evidence

A complete local validation was run after documentation alignment:

```text
508 tests passed
0 failures
0 skipped tests reported
compileall passed
git diff --check passed
git diff --cached --check passed
```

The run included dedicated coverage for:

- memory and document search boundaries;
- checkpoint persistence, retry, continuity, and conflict immutability;
- Developer Bridge input, output, filesystem, scope, and injection safety;
- SQLite health-check connection closure;
- Memory Core, Review Gate, Loop Learning, Agent Runtime, Bridge, MCP, SessionStore, and procedure regressions.

No SQLite `ResourceWarning` appeared in the validation output.

## Review findings

| Severity | Count | Result |
|---|---:|---|
| Critical | 0 | PASS |
| Major | 0 | PASS |
| Minor | 0 | PASS |
| Suggestion | 1 | Run the separately gated remote macOS CI before any merge or release claim |

## Gate decision

Stage 17 passes local macOS integration review.

The repository is eligible for a documentation-alignment commit and then, only on explicit user request, branch push and Draft PR macOS CI. Until those operations occur and their evidence is reviewed:

- `release_readiness` remains `in_progress`;
- `feature_branch_push` remains `not_pushed`;
- `draft_pr` remains `not_created`;
- `remote_macos_ci` remains `not_run`;
- merge, tag, GitHub Release, and deployment remain blocked by later human gates.
