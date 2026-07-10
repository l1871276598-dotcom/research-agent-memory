# Phase 0 Contract — Conversation Archive & Provenance

**Version**: 0.1.0
**Status**: frozen (Phase 3 implementation)
**Frozen**: 2026-07-07

---

## 1. Archive Pipeline

```
ChatGPT / Claude conversation
        │
        ▼
  Export / manual save
        │
        ▼
  4-archive/conversations/{source}/
  (immutable raw evidence)
        │
        ▼
  LAOS import scanner
  (message-level dedup)
        │
        ▼
  Knowledge extraction
  (facts, decisions, principles, friction)
        │
        ▼
  candidate → Review Gate
        │
        ▼
  Promoted to formal directories
  (with provenance chain)
```

## 2. Raw Conversation Storage

```
4-archive/conversations/
├── chatgpt/
│   └── conv_2026-07-07_abc123.json    # ChatGPT export format
└── claude/
    └── conv_2026-07-07_def456.json    # Claude export format
```

Raw conversations are:
- **Immutable** — never modified after import
- **Append-only** — new exports added, never overwritten
- **Evidence** — the ground truth for all derived knowledge

## 3. Conversation Frontmatter

```yaml
---
id: "01J7..."
schema_version: 1
type: conversation
source: chatgpt | claude
conversation_id: "conv_abc123"
title: "Discussion about PDC paper revisions"
exported_at: 2026-07-07T14:00:00Z
message_count: 42
created: 2026-07-07
tags:
  - pdc
  - manuscript
---
```

## 4. Message-Level Dedup

On import:
1. Compute `message_hash` for each message (SHA-256 of normalized content)
2. Check against `conversation_messages` table
3. Skip duplicate messages (same conversation_id + message_hash)
4. New messages → append to raw file, index for extraction

```sql
CREATE TABLE conversation_messages (
    message_id     TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_hash   TEXT NOT NULL,
    role           TEXT,        -- user | assistant | system
    content        TEXT,
    timestamp      TEXT,
    indexed_at     TEXT
);
```

## 5. Knowledge Extraction

From each conversation, LAOS extracts:

| Extract Type | Target Note Type | Example |
|---|---|---|
| Factual claim | `principle` or `reference` | "User confirmed RMRE abstract limit is ≤250 words" |
| Decision | `decision` | "Decided to use Option C for Data Availability" |
| Friction / error pattern | `principle` | "Stage 10 blocked because author facts were missing" |
| Workflow rule | `principle` | "Never auto-upload without upload_authorized=true" |
| Project status change | `project` update | "PDC paper: Stage 4 passed, Stage 10 blocked" |

Extraction is **candidate-only**. Nothing enters active memory without Review Gate.

## 6. Derived Knowledge Provenance Format

Every extracted note MUST carry:

```yaml
derived_from:
  source: chatgpt | claude
  conversation_id: "conv_abc123"
  message_ids:
    - "msg_001"
    - "msg_003"
    - "msg_007"
  source_hash: "sha256:def456..."
  extracted_at: 2026-07-07T15:00:00Z
  extractor_version: "1.0.0"
```

This ensures:
- Every claim is traceable to its source conversation
- If source conversation is updated or corrected, derived notes can be re-evaluated
- Audit trail is complete from answer → note → conversation → message

## 7. Separation of Evidence and Knowledge

```
Raw conversation (4-archive/conversations/)
    │
    │  NEVER modified
    │  Evidence only
    │
    ▼
Extracted knowledge (candidate)
    │
    │  Can be: accepted, rejected, merged, revised
    │  Always carries derived_from provenance
    │
    ▼
Promoted knowledge (1-projects/, 2-areas/, 3-resources/)
    │
    │  Active, searchable, trusted
    │  Provenance chain intact
```

## 8. Re-extraction on Source Update

If a conversation export is updated (e.g., ChatGPT re-export with more messages):

1. New messages → extracted as new candidates
2. Existing derived notes → flagged for re-review (source hash changed)
3. Human decides: keep, update, or retire derived notes
4. Old derived notes → retain with `superseded_by` pointer if replaced

## 9. Phase 3 Scope

Phase 3 delivers:
- [ ] Raw conversation import (ChatGPT JSON export format first)
- [ ] Message-level dedup
- [ ] Knowledge extraction → candidate
- [ ] Provenance chain in all derived notes
- [ ] Re-extraction detection on source update

Phase 3 does NOT deliver:
- Real-time conversation capture (no browser extension)
- Automatic extraction without human review
- Claude conversation import (ChatGPT first, Claude second)
