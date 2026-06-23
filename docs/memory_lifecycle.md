# Agent Memory Lifecycle

This repository keeps Markdown and raw imports as the source of truth. SQLite is a local, rebuildable cache.

## Data Flow

```text
imports/chatgpt/conversations/      permanent ChatGPT raw markdown
imports/manual/raw/                 permanent manual raw files
memory/recent/                      30-day normalized recent text
memory/<type>/                      long-term memory/v1 or memory/v2
local memory.sqlite                 FTS5, links, and distillation audit cache
```

Manual files enter through `imports/manual/inbox/` or `memory_tools.py import-manual --file`. Supported text extraction includes `md`, `txt`, `json`, `csv`, `html`, and `docx`; `pdf` and `rtf` use local extractors when available and otherwise archive raw without pretending text extraction succeeded.

ChatGPT exports enter through `chatgpt_export_sync.py import-zip`. Re-importing unchanged conversations does not rewrite recent files or reset retention.

## Schema

Old memories without `schema` remain valid as `memory/v1`. New long-term agent memories use `memory/v2`. Recent bodies use `recent/v1`.

Authoritative relations are stored in frontmatter as strings:

```yaml
relations:
  - "supports:decision-no-gui"
```

Allowed relations are `belongs_to`, `applies_to`, `depends_on`, `derived_from`, `supports`, `contradicts`, `supersedes`, `superseded_by`, and `related_to`.

Markdown body links such as `[[id]]`, `[[id#heading]]`, and `[[id^block]]` are indexed as auxiliary links. Frontmatter relations are the authority when natural-language body text disagrees.

## SQLite

`memory.py db-init` creates schema version 2 with:

- `memories`
- `memory_fts`
- `index_state`
- `links`
- `distillation_audit`

`memory.py db-rebuild` validates Markdown, builds a temporary database, checks consistency, and atomically replaces the old database only after success.

## Distillation

`memory_distill.py prepare` selects due, unprotected recent files and creates isolated task directories under the local state directory. `run` executes a non-interactive Codex-compatible command inside the task directory and accepts only `distillation_result.json`.

`apply` validates JSON schema, source ID, source hash, candidate action, relations, and current recent hash before writing long-term memory. Codex never writes official memory, raw files, or SQLite directly.

## Deletion Gate

After successful apply, recent memory enters `pending_delete` with `delete_after = distilled_at + 7 days`.

`purge` deletes a recent file only when all gates pass:

- audit row is `pending_delete`;
- `delete_after` has arrived;
- current recent hash matches the audited hash;
- source raw file still exists;
- record is not protected;
- full Markdown validation passes;
- distillation result has no unresolved conflicts.

Raw files are retained permanently.
