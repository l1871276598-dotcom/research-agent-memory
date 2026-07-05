# AGENTS.md

## Project Rules

1. Follow the minimum-code principle: use as little code, as few files, classes, dependencies, and abstractions as possible to implement the current function.
2. Complete only the current explicit task each time; do not pre-implement later stages.
3. Do not create GUI, web frontend, desktop application, or other interface layers.
4. Markdown and JSONL are the authoritative data sources.
5. SQLite, Chroma, cache, and index files must be deletable and rebuildable.
6. Active SQLite and Chroma data must not be stored in iCloud data directories.
7. Personal data, work data, internal data, and restricted data must be isolated.
8. Do not overwrite confirmed memories; conflicting information must be preserved and clearly marked.
9. Do not automatically run git commit or git push.
10. Run existing tests after every modification.
11. Prefer the Python standard library.
12. The active implementation and acceptance target is macOS with Python 3.11 or later. Linux and Windows compatibility is deferred until all planned functions are implemented, integrated, and accepted on macOS; deferred platforms must not block current delivery.
13. Commands must return a non-zero exit code when execution fails.
14. Do not modify files outside the current task scope.
15. Do not introduce LangChain, LangGraph, Mem0, cloud databases, or paid APIs without an explicit request.
16. Do not commit real memories, PDFs, databases, secrets, or internal materials to GitHub.

## Completion Report

Every task completion report must include:

- Modified files
- Implemented content
- Checks or tests executed
- Check or test results
- Unfinished content
