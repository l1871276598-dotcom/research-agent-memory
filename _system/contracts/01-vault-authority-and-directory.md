# Phase 0 Contract — Vault Authority & Directory

**Version**: 0.1.0
**Status**: frozen
**Frozen**: 2026-07-07

---

## 1. Authority Boundaries

| Data | Authoritative Source | Notes |
|---|---|---|
| Note body, human tags, project assignment | Vault Markdown | Human owns content |
| Full-text index, backlinks, derived relations | LAOS (SQLite FTS5) | Rebuildable from vault |
| candidate, audit log, quarantine, verification receipt | LAOS | Never in frontmatter |
| Agent-generated new content | `0-inbox/laos-generated/` | Pending Review Gate |
| Approved formal knowledge | Promoted to formal dirs by Review Gate | Atomic promotion only |

**LAOS never modifies human-authored files.** The only write paths for LAOS are:
1. `0-inbox/laos-generated/` — new candidates
2. Formal directories (`1-projects/`, `2-areas/`, `3-resources/`) — only via Review Gate atomic promotion
3. `4-archive/conversations/` — append-only conversation evidence
4. `_system/generated/moc/` — auto-generated MOC indices

---

## 2. Directory Structure

```
vault/
├── 0-inbox/
│   ├── human/              ← Human quick-capture, LAOS reads only
│   └── laos-generated/     ← LAOS output, pending review
├── 1-projects/             ← Active project notes (human + promoted)
├── 2-areas/                ← Principles, decisions, contexts
├── 3-resources/            ← References, literature
├── 4-archive/
│   ├── conversations/
│   │   ├── chatgpt/        ← Raw ChatGPT exports (append-only)
│   │   └── claude/         ← Raw Claude exports (append-only)
│   └── ...                 ← Archived projects/notes
├── _system/
│   ├── contracts/          ← This Phase 0 contract
│   ├── generated/
│   │   └── moc/            ← LAOS-generated MOC indices
│   ├── templates/          ← Obsidian templates
│   └── dashboards/         ← Generated views
├── memory.md               ← Human-maintained, LAOS read-only
├── projects.md             ← Human-maintained, LAOS read-only
├── tasks.md                ← Human-maintained, LAOS read-only
├── notes.md                ← Human-maintained raw inbox
└── personality.md          ← Human-maintained
```

---

## 3. Write Ownership Table

| Path | Writer | Rule |
|---|---|---|
| `0-inbox/human/` | Human | LAOS reads, never writes |
| `0-inbox/laos-generated/` | LAOS | New candidates only |
| `1-projects/` | Human + LAOS promotion | LAOS only via Review Gate atomic promotion |
| `2-areas/` | Human + LAOS promotion | Same as above |
| `3-resources/` | Human + LAOS promotion | Same as above |
| `4-archive/conversations/` | LAOS | Append-only, never modify |
| `_system/generated/moc/` | LAOS | Auto-generated, overwrite allowed |
| `_system/contracts/` | Human | Frozen after Phase 0 |
| `memory.md` | Human | LAOS read-only |
| `projects.md` | Human | LAOS read-only |
| `tasks.md` | Human | LAOS read-only |

---

## 4. Lifecycle vs Verification State

**lifecycle** (in frontmatter, for Obsidian display):
- `active` — currently relevant
- `archived` — moved to archive

**verification_state** (in LAOS DB only, never in frontmatter):
- `candidate` — generated, pending review
- `verified` — passed Review Gate
- `quarantined` — failed validation, held
- `tombstone` — deleted from vault, index retained for audit

`status: active` in frontmatter does NOT imply LAOS trust. Trust is determined by LAOS DB verification_state + receipt.

---

## 5. Note Identity Rules

- **ULID preferred**: new notes created via Obsidian Templater get a ULID `id` in frontmatter
- **No id fallback**: LAOS uses `rename_fingerprint` (normalized content hash excluding volatile fields) for heuristic rename detection
- **LAOS never injects id into human files**
- **Legacy backfill**: Phase 2, human-approved one-time operation with audit record
- **Rename detection**: id match → same note. No id → fingerprint heuristic. Uncertain → fail-closed (tombstone old + index new, flag for human review)

---

## 6. Non-Goals (explicitly excluded)

- Real-time file watching
- Vector database / embeddings
- GUI
- Bidirectional relations backfill into human notes
- ChatGPT HTTP bridge (deferred to Phase 4+)
- Remote entry (Feishu/Telegram, deferred)

---

## 7. Environment Prerequisites

- LAOS runs on the Mac where vault is fully synced
- Vault directory set to "Keep Downloaded" in Finder (not `brctl download`)
- Scanner detects `.icloud` placeholder files and skips with log
