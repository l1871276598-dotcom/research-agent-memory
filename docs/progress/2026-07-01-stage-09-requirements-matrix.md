# LAOS v0.8 Requirements Traceability Matrix

Date: 2026-07-01
Status: complete

| Requirement | Status | Direct evidence |
|---|---|---|
| Minimal executable LAOS kernel | PASS | `src/laos.py`, `build_application()` |
| Exactly five exposed Agent types | PASS | `src/agents/registry.yaml`, `AgentRegistry.from_config()` |
| Exact task-type routing | PASS | `src/agents/registry.py`; registry duplicate and malformed-config tests |
| Strict Agent result envelope | PASS | `src/agents/base.py`; BaseAgent and result-schema tests |
| Routing-only Orchestrator | PASS | `src/orchestrator/orchestrator.py`; router-only source audit test |
| Orchestrator requests Context before target Agent | PASS | Orchestrator event-order tests |
| Import capability is an Agent | PASS | `ImportAgent`; real import adapter tests |
| Memory creation is an Agent and candidate-only | PASS | `MemoryAgent`, `CandidateStore`; status-bypass tests |
| Search capability is an Agent | PASS | `SearchAgent`, `MemoryCore.search()` |
| Review capability is an Agent | PASS | `ReviewAgent`, `ReviewGate` |
| Context capability is an Agent | PASS | `ContextAgent`, `ContextBuilder` |
| Memory Core exposes no activation method | PASS | `src/memory/core.py`; API-surface tests |
| Canonical lifecycle states and transitions | PASS | `src/memory/lifecycle.py`; immutability and invalid-transition tests |
| Review Gate is sole application activation gateway | PASS | private authority token and bypass tests |
| Only accept/reject/merge/conflict are allowed | PASS | `REVIEW_ACTIONS`; exact-action tests |
| Legacy review commands route through Review Agent/Gate | PASS | public legacy handler translation tests |
| Candidate and target are revalidated at review time | PASS | stale-target, tampering, partition, and source-hash tests |
| Review is workspace-bound | PASS | `ReviewAgent` and `ReviewGate`; `tests/test_laos_audit.py` |
| Restricted records never enter LAOS search/context | PASS | `MemoryStore.active_relevant`; restricted leakage tests |
| Candidate/inactive records never enter context | PASS | active-only store filter and context tests |
| Context is deterministic and bounded | PASS | `ContextBuilder`; deterministic, dedupe, and budget tests |
| No full-store fallback in Context Builder | PASS | fake store test rejects `records()` access |
| CLI accepts one JSON task source | PASS | mutually exclusive `--task-json`/`--task-file` parser |
| CLI success output is canonical one-line JSON | PASS | pipeline canonical-output tests |
| CLI failure is safe JSON and nonzero | PASS | generic error, no traceback/path disclosure tests |
| UTF-8 BOM task file support | PASS | task-file BOM test |
| Clean candidate to review to active pipeline | PASS | end-to-end pipeline test |
| Existing v0.7 behavior remains compatible | PASS | complete 317-test regression suite |
| Standard-library-only v0.8 implementation | PASS | source inspection; no new runtime dependency |
| No GUI, service, vector DB, cloud API, or Meta Planner | PASS | source tree and documented scope |
| No automatic accept | PASS | candidate-only creation and explicit review pipeline |
| No automatic commit or push | PASS | Git remains uncommitted; project rule retained |
| No real memories, databases, PDFs, logs, or secrets in changes | PASS | Git status and artifact review |
| Architecture audit | PASS | `docs/progress/2026-07-01-stage-07-audit.md` |
| Local-only security audit | PASS | `docs/progress/2026-07-01-stage-08-security-audit.md` |
| Python regression | PASS | 317 tests passed in 46.146 seconds through Developer Bridge |

## Explicitly out of scope

- MCP, HTTP, web, GUI, desktop UI, or background service
- Owner identity verification and multi-user authorization
- Automatic acceptance or autonomous policy application
- Vector embeddings and semantic/hybrid ranking implementation
- Full Meta Planner or autonomous retry loop
- Built-in Zotero/EndNote/PDF literature system expansion
- Literature integration beyond a future external interface

## Remaining release-document cleanup

README still needs three narrow textual corrections before the final release report can be marked clean:

1. use canonical `conflicted` for new v0.8 lifecycle descriptions while identifying `conflict` as legacy-readable only;
2. replace the final stray v0.7.0 scope reference with v0.8.0;
3. mark literature directories/index wording as legacy compatibility and state that future literature work is external-interface-only.
