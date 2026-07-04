# Hermes selective source port into LAOS

## Final decision

Hermes is an upstream reference repository, not a LAOS runtime dependency.

LAOS will not:

- install or launch Hermes;
- vendor the complete Hermes repository;
- preserve `HERMES_HOME`, Hermes profiles or a second Hermes state database;
- expose Hermes as a separate CLI or product;
- automatically overwrite LAOS with upstream updates.

LAOS will selectively port useful source code, algorithms, prompts, tests and safety mechanisms into native LAOS modules. Each migrated component must use LAOS data ownership, schemas, review gates and runtime entry points.

## Porting rules

1. Start from the existing LAOS architecture.
2. Copy an upstream function or module directly when it is isolated and its contract fits LAOS.
3. Adapt only imports, paths, schemas, permissions and persistence boundaries required by LAOS.
4. Do not rewrite an existing Hermes mechanism merely to make it look different.
5. Do not copy unrelated dependencies to satisfy one small function; isolate the useful part instead.
6. Preserve source attribution and the MIT license notice for substantial copied portions.
7. Add LAOS tests before marking a component as integrated.
8. Long-term memory and behavioral changes must pass the LAOS Review Gate.
9. `restricted` content must never be passed into model context or external tools.
10. Features 2, 12 and 21 remain excluded: general multi-provider model management, Agent Cron, and trajectory/training-data systems.

## Component registry

`config/hermes_upstream_components.json` records:

- the Hermes paths watched for each feature;
- the LAOS destination modules;
- whether direct copying or adaptation is expected;
- implementation status;
- the last reviewed upstream commit.

This registry is the single source of truth for Hermes-derived work.

## Update review

Run locally:

```bash
python3 tools/check_hermes_updates.py check \
  --markdown hermes-upstream-review.md
```

The checker compares the last reviewed Hermes commit with the current upstream branch, filters changes by tracked component paths and classifies relevant files. It does not copy or apply any source.

After the upstream changes have been reviewed and selected ports are complete:

```bash
python3 tools/check_hermes_updates.py mark-reviewed \
  --commit <40-character-reviewed-commit>
```

A manual-only GitHub Actions workflow, `Review Hermes upstream`, generates the same report as a downloadable artifact and job summary. There is no scheduled Cron.

## Implementation order

### Phase 1 — automatic conversation review

Primary upstream source:

- `agent/background_review.py`

LAOS target:

- `src/reflection/conversation_review.py`

Port:

- review prompts;
- compact digest for routed review models;
- trigger counters;
- restricted tool surface;
- memory/skill decision separation.

Change:

- all memory results become LAOS candidates;
- no direct active-memory write;
- all accepted memories pass Quality and Review gates.

### Phase 2 — memory provider lifecycle and context fencing

Primary upstream sources:

- `agent/memory_provider.py`
- `agent/memory_manager.py`

LAOS targets:

- `src/memory/provider.py`
- `src/context/fencing.py`

Port only the provider lifecycle, context sanitization, fenced injection and background synchronization patterns. Existing LAOS MemoryCore and ContextBuilder remain authoritative.

### Phase 3 — skills and curator

Primary upstream sources:

- `tools/skill_manager_tool.py`
- `agent/skill_commands.py`
- `agent/curator.py`

LAOS targets:

- `src/skills/store.py`
- `src/skills/manager.py`
- `src/skills/curator.py`

Persistent skill changes require diff, provenance, backup and review.

### Phase 4 — runtime, tools and Codex

Port the smallest coherent runtime and tool contracts needed for LAOS. General provider management remains excluded. The first model path is ChatGPT/Codex OAuth.

### Phase 5 — sessions, MCP, plugins and security

Port session search, MCP exposure, plugin contracts, approvals and tool guardrails. These must use the LAOS workspace, project and confidentiality model.

### Phase 6 — optional capabilities

Subagents, Kanban, gateways and non-local execution environments are last. They may only be enabled after single-agent permissions, context isolation and review boundaries are stable.
