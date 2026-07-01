# Hermes source integration into LAOS

## Decision

LAOS does not run Hermes as a separately installed external product. Selected upstream Hermes Agent source is copied into this repository under `vendor/hermes_agent/` and loaded as an internal LAOS runtime.

The upstream source snapshot is preserved as closely as possible. LAOS-specific behavior belongs under `src/`; files under `vendor/hermes_agent/` must not be edited manually. This keeps upstream comparison and future synchronization deterministic.

## Ownership boundary

- LAOS remains the authority for persistent memory, evidence, candidates, review, active memory, confidentiality and Context Packs.
- Vendored Hermes code supplies the agent loop, Codex runtime, tools, skills, curator, sessions, subagents, gateways, execution backends, MCP/plugin machinery, security controls, profiles and operational utilities.
- Hermes `MEMORY.md` and `USER.md` cannot bypass the LAOS Review Gate. Their writes must be intercepted or treated as non-authoritative cache projections.

## Excluded surfaces

The user explicitly excluded the previously numbered features 2, 12 and 21:

1. Multi-provider model configuration is not exposed by LAOS. A minimal Codex/ChatGPT OAuth execution path may remain because an agent still requires a model runtime.
2. Agent Cron and cron provider plugins are not vendored or registered.
3. Trajectory capture, batch training-data generation and trajectory compression are not vendored or registered.

Some shared upstream implementation files may remain when they are required by the agent loop. Presence in the source snapshot does not mean LAOS exposes the rejected feature.

## Repository layout

```text
config/hermes_vendor.json        upstream pin and copy policy
tools/sync_hermes_vendor.py      deterministic source snapshot synchronizer
vendor/hermes_agent/             generated upstream source; do not hand-edit
src/runtime/hermes.py            LAOS bootstrap and state scoping
src/agents/runtime.py            LAOS runtime status/control task surface
```

The vendor root is inserted into `sys.path` unchanged because upstream Hermes uses absolute imports such as `agent`, `tools`, `gateway` and `hermes_cli`. Nesting and rewriting every import would create a permanent fork and make upstream updates substantially harder.

Hermes supports `HERMES_HOME` and a context-local home override. LAOS uses the context-local override so vendored runtime state can be kept inside the LAOS state directory without changing upstream code.

## Initial commands

Check upstream state:

```bash
python3 tools/sync_hermes_vendor.py check
```

Copy the pinned/latest source snapshot into the repository:

```bash
python3 tools/sync_hermes_vendor.py sync
python3 tools/sync_hermes_vendor.py verify
```

Query the LAOS runtime registration after initializing the data root:

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"runtime.hermes.status","workspace":"personal","input":{}}'
```

## Upstream update process

The `Sync Hermes upstream` GitHub Actions workflow is manual-only. It:

1. resolves the current upstream commit or a supplied ref;
2. downloads the official source archive;
3. copies the allowlisted source while excluding rejected surfaces;
4. records archive and source-tree hashes;
5. runs LAOS compile and unit checks;
6. opens a draft update pull request.

No scheduled runtime Cron is introduced. An update remains a normal reviewed source change.

## Integration stages

### Stage A — vendor foundation

- source manifest and synchronizer;
- source integrity metadata;
- internal runtime bootstrap;
- runtime status task;
- license notice;
- manual upstream update workflow.

### Stage B — trusted memory bridge

- adapt upstream memory prefetch to `ContextBuilder`;
- route post-turn facts to LAOS candidates;
- prevent direct active-memory writes;
- preserve restricted-memory exclusion;
- map session evidence into LAOS.

### Stage C — skills and reflection

- preserve upstream Skill and Curator implementations;
- route durable rule proposals through LAOS candidates;
- require review before persistent behavioral changes;
- add backup, diff and rollback validation.

### Stage D — remaining runtime capabilities

- tools and execution backends;
- Codex runtime and OAuth;
- MCP and plugin loading;
- subagents and Kanban;
- gateways and optional dashboard;
- profile isolation and security checks.

Each stage must use upstream modules directly where their contracts fit. LAOS adapters should contain only ownership, permission, path and schema translation logic.
