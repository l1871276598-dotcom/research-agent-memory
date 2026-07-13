---
name: research-memory
description: Use when 用户需要查询、召回、导入、验证或安全维护本地 Research Agent Memory，包括项目记忆、原则、决策、会话、PDC 论文、Journal Manuscript Agent、血糖记录或手动文件导入。
---

# Research Memory

## Default Paths

- Repository: derived from this script's repository checkout
- Memory root: `~/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent`
- Local state: `~/Library/Application Support/ResearchAgent`

Use `scripts/memoryctl.py` first. It is a thin wrapper over the repository CLIs and honors `RESEARCH_MEMORY_REPO`, `RESEARCH_MEMORY_ROOT`, and `RESEARCH_MEMORY_STATE` when a task needs alternate paths.

## Common Commands

Run commands from this skill directory or pass the script by absolute path:

```bash
python3.11 scripts/memoryctl.py search "PDC"
python3.11 scripts/memoryctl.py context "PDC project" --project pdc
python3.11 scripts/memoryctl.py recall "PDC project"
python3.11 scripts/memoryctl.py validate
python3.11 scripts/memoryctl.py index
python3.11 scripts/memoryctl.py doctor
python3.11 scripts/memoryctl.py project-status pdc
python3.11 scripts/memoryctl.py import-file "/path/with spaces/file.docx"
python3.11 scripts/memoryctl.py import-file "/path/file.docx" --dry-run-only
python3.11 scripts/memoryctl.py add --type principle --title "..." --scope global --workspace personal --confidentiality personal --source user --confidence confirmed --content "..."
python3.11 scripts/memoryctl.py status
```

Use `context` for a bounded Agent Context Pack. `recall` is a compatibility alias for the same v0.7 context command. Search, context, doctor, project status, validation, and status are safe read-only operations.

## Safety Rules

- Before any write, run search or context first to avoid duplicates and preserve conflicting confirmed memories.
- After any write, run `validate`, `index`, `doctor`, and context to prove the new content is reachable.
- Before editing an existing long-term memory, back up the target file; on failure, restore the backup, re-index, and report the rollback.
- Preserve raw evidence permanently. Do not delete or rewrite `imports/chatgpt/conversations/` or `imports/manual/raw/`.
- Do not modify SQLite directly. Treat SQLite, cache, and index files as rebuildable derived state.
- Do not run schema migration, database replacement, or high-risk distillation apply unless the user explicitly asks and the gate checks are satisfied.
- Do not upload memory content to the network or commit real memories, PDFs, raw data, backups, SQLite, secrets, health records, or internal material to Git.
- Do not create or rewrite relations in bulk from keywords alone. Confirm source and target and preserve conflicts.
- For medical or health content, record only user-provided facts unless explicitly asked for general information; do not create diagnoses or treatment conclusions.
- In outputs, quote only the minimum memory text needed to answer the user and avoid leaking unrelated sensitive content.
