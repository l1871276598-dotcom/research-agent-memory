# Phase 0 Contract — Review Gate, Verification & Context Pack

**Version**: 0.1.0
**Status**: frozen
**Frozen**: 2026-07-07

---

## 1. Review Gate (unchanged from LAOS v0.9.0)

- Agent output → **candidate only**
- candidate → human review → **accepted** or **rejected**
- Accepted → **atomic promotion** to formal directory
- Rejected → logged, not indexed as active
- No automatic promotion under any circumstance

## 2. Verification State Machine

```
                    ┌─────────┐
                    │candidate│
                    └────┬────┘
                         │ human review
              ┌──────────┼──────────┐
              ▼                     ▼
        ┌─────────┐           ┌──────────┐
        │verified │           │ rejected  │
        └────┬────┘           └──────────┘
             │
             │ file deleted from vault
             ▼
        ┌─────────┐
        │tombstone│
        └─────────┘

quarantine: entered on scan failure, exited on human fix + re-scan
```

All state transitions are logged with timestamp, operator, and reason.

## 3. Context Pack Format

When an Agent queries LAOS, the response is a **context pack**, not raw text:

```json
{
  "pack_id": "cp_01J7RM4A8KX8P1Z2Y3W4V5B6N7",
  "generated_at": "2026-07-07T14:00:00Z",
  "generator_version": "1.0.0",
  "query": "PDC drilling fluid immersion",
  "trust_level": "verified_context_pack",
  "manifest_head": "sha256:...",
  "canonicalizer_version": "1.0.0",
  "validated_files": [
    {
      "path": "1-projects/PDC 论文稿件.md",
      "evidence_hash": "sha256:abc123...",
      "verification_state": "verified",
      "indexed_at": "2026-07-07T12:00:00Z"
    }
  ],
  "quarantined_files": [],
  "unverified_sources": [],
  "contents": [
    {
      "path": "1-projects/PDC 论文稿件.md",
      "note_id": "01J7...",
      "type": "project",
      "title": "PDC–rock Drilling-Fluid Immersion Manuscript",
      "body": "...",
      "frontmatter": {},
      "links": ["[[钻井液]]", "[[PDC钻头]]"]
    }
  ]
}
```

## 4. Verification Receipt (embedded in context pack)

```json
{
  "pack_id": "cp_01J7...",
  "generated_at": "2026-07-07T14:00:00Z",
  "generator_version": "1.0.0",
  "manifest_head": "sha256:...",
  "canonicalizer_version": "1.0.0",
  "validated_files": ["1-projects/PDC 论文稿件.md"],
  "source_hashes": {
    "1-projects/PDC 论文稿件.md": "sha256:abc123..."
  },
  "quarantined_files": [],
  "unverified_sources": []
}
```

## 5. Trust Levels

Agents MUST distinguish between:

| Level | Enum Value | Meaning |
|---|---|---|
| Verified | `verified_context_pack` | All files passed Review Gate, hashes match, receipt attached |
| Unverified | `unverified_search_results` | FTS5 hit but no receipt or file not yet reviewed |
| Raw | `plain_search_results` | String match only, no provenance guarantee |

## 6. Agent Behavior Contract

When consuming a context pack, the Agent MUST:

1. **Check trust_level** before using content
2. **Cite source files** in response (path + note_id)
3. **Refuse to answer** if only `unverified_search_results` are available and the question requires verified knowledge
4. **Never silently pick** between conflicting memories — flag the conflict
5. **Never include** quarantined content in answers
6. **Trace** every claim back to source file or conversation evidence

## 7. Acceptance Tests (Phase 4)

1. Agent cites context pack sources in response
2. Agent behavior differs with vs without context pack
3. Agent refuses to silently resolve conflicting memories
4. Quarantined content does not appear in answers
5. Answer traceable to original Markdown or conversation evidence
