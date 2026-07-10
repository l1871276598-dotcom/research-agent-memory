# Phase 0 Contract — Note Identity & Frontmatter Schema

**Version**: 0.1.0
**Status**: frozen
**Frozen**: 2026-07-07

---

## 1. Minimal Frontmatter (Human Notes)

```yaml
---
id: "01J7RM4A8KX8P1Z2Y3W4V5B6N7"   # ULID, optional for human notes
schema_version: 1
type: project | principle | decision | meeting | reference | literature | context
lifecycle: active | archived          # Obsidian display only
source: human | chatgpt | claude      # Provenance hint, not trust proof
project: pdc-rock-paper               # Optional, project slug
created: 2026-07-07
updated: 2026-07-07
tags:
  - drilling
  - manuscript
---
```

## 2. Field Definitions

| Field | Required | Writer | Notes |
|---|---|---|---|
| `id` | No | Human (Templater) | ULID. LAOS never injects. |
| `schema_version` | Yes | Human | Bump triggers full re-parse |
| `type` | Yes | Human | From closed enum |
| `lifecycle` | Yes | Human | `active` / `archived`. Display only. |
| `source` | No | Human | `human` / `chatgpt` / `claude` |
| `project` | No | Human | Project slug for grouping |
| `created` | Yes | Human | ISO date |
| `updated` | Yes | Human | ISO date |
| `tags` | No | Human | Flat list |

## 3. Forbidden Fields (never in frontmatter)

- `status` (ambiguous — use `lifecycle`)
- `verified` / `verification_state` (LAOS DB only)
- `confidence` (LAOS DB only)
- `audit_status` (LAOS DB only)
- `relations` (derived from `[[links]]` in body by LAOS)

## 4. LAOS-Generated Note Frontmatter

```yaml
---
id: "01J7..."                        # LAOS-generated ULID
schema_version: 1
type: project | principle | decision | reference
lifecycle: active
source: laos
generated_by: "laos-v0.9.0"
run_id: "run_01J7..."
created: 2026-07-07
updated: 2026-07-07
project: pdc-rock-paper
tags: []
---
```

LAOS-generated notes always have `id` and `run_id`.

## 5. Conversation Archive Frontmatter

```yaml
---
id: "01J7..."
schema_version: 1
type: conversation
source: chatgpt | claude
conversation_id: "conv_abc123"
exported_at: 2026-07-07T14:00:00Z
message_count: 42
created: 2026-07-07
---
```

## 6. Derived Knowledge Provenance

```yaml
derived_from:
  source: chatgpt
  conversation_id: "conv_abc123"
  message_ids: ["msg_1", "msg_3", "msg_7"]
  source_hash: "sha256:abc123..."
```

## 7. Schema Version Policy

- `schema_version: 1` — current
- Bumping schema_version triggers full re-parse of all notes
- New `type` values require schema_version bump
- Field additions (optional) do NOT require bump
- Field removals or semantic changes DO require bump
