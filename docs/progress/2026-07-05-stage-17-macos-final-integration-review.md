# Stage 17 — macOS Final Integration Review

Date: 2026-07-05
Decision: PASS for local macOS integration; PR #29 merged
Release target: v0.9.0
Current repository metadata: v0.9.0-development
Current platform scope: macOS only
Reviewed feature branch: feat/laos-memory-search-tool
Reviewed feature HEAD: 716a11b98ee6b658039096c53b14aca4d1223811
PR #29 head: a25e43802258f2adf421f8b55e6e2193ca43755a
Post-merge main HEAD: c21da74d9027760a47f1d96aa626184e6616d3f1

## Review purpose

Stage 17 closes the documentation and integration gap after the earlier Stage 16 review. It covers the feature-branch additions that were not present in the Stage 16 evidence and verifies them against the active macOS-first delivery decision.

The original Stage 17 decision authorized only remote-review preparation. The feature branch was subsequently pushed, PR #29 completed both remote macOS CI checks successfully, and PR #29 was squash merged into `main`. This review record does not authorize or claim a v0.9.0 tag, GitHub Release, or deployment.

## Incremental scope after Stage 16

The review covers the following feature-branch additions:

- `5601378` — stable LAOS memory-root error classification
- `217c81a` — Agent document search integration
- `fc90ab1` — read-only LAOS memory search tool
- `e8279e5` — bounded memory search result limits
- `ad1c553` — checkpoint continuity acceptance
- `9f07c53` — macOS-first delivery scope and macOS CI
- `fad8852` — explicit SQLite health-check connection closure
- `716a11b` — fixed-scope Developer Bridge Adapter and regression coverage
- documentation alignment in README, ROADMAP, PHASE_STATUS, and this review record

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

The delivery sequence completed through merge is:

```text
local macOS integration
→ branch commit
→ branch push
→ PR #29 macOS CI
→ merge to main
```

The remaining release sequence is separately gated:

```text
v0.9.0 tag
→ GitHub Release
→ deployment, if separately authorized
```

Completion of the merge gate does not imply completion of any release gate.

## Verification evidence

A complete local validation was run after the original documentation alignment:

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

Remote evidence for PR #29 records two completed macOS CI checks with successful conclusions before the squash merge.

## Review findings

| Severity | Count | Result |
|---|---:|---|
| Critical | 0 | PASS |
| Major | 0 | PASS |
| Minor | 0 | PASS |
| Suggestion | 1 | RESOLVED — the remote-CI prerequisite was satisfied before merge |

## Gate decision

Stage 17 passed local macOS integration review. The subsequent delivery gates also completed through the PR #29 squash merge into `main` at `c21da74d9027760a47f1d96aa626184e6616d3f1`:

- `feature_branch_push` is `pushed`;
- PR #29 is `merged`;
- `remote_macos_ci` is `passed` with two successful checks;
- `main_merge` is `passed`.

The release gates remain open:

- `release_readiness` remains `in_progress`;
- the `v0.9.0` tag has not been created;
- no GitHub Release has been published;
- no deployment has been performed.
