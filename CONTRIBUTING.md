# Contributing to LAOS

Thank you for contributing. LAOS accepts bounded changes to its local-first
memory core, deterministic learning and review paths, adapters, tests, and
documentation.

## Before making a change

- Open an issue before proposing changes to architecture, schemas, security
  boundaries, or documented behavior. Suspected vulnerabilities must follow
  [the security policy](SECURITY.md) instead of a public issue.
- Keep implementation and acceptance within macOS and Python 3.11 or later.
- Do not add a cloud database, paid API, large framework, GUI, or speculative
  abstraction without an approved design.
- Never include real memories, ChatGPT exports, unpublished documents, PDFs,
  databases, logs, credentials, tokens, or other private data.

## Local setup

The core uses the Python standard library. From the repository root, run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests tools
```

Optional MCP dependencies are isolated in `requirements-mcp.txt` and are not
required for the standard-library core checks.

## Pull requests

- Address one bounded problem.
- Describe the behavior being changed and any security boundary it affects.
- Add tests for behavior changes.
- Preserve candidate-only creation and the explicit Review Gate's sole
  authority to activate memory.
- Before submitting, run the full test suite, the `compileall` command above,
  and `git diff --check`.
- Do not include unrelated refactors, generated data, or private data.

A documentation-only correction may omit new tests when the existing suite and
the documented checks pass.

## Design principles

Prefer deterministic behavior, human authority above automation, evidence
before governance, fail-closed boundaries, and the minimum code needed.
Security-sensitive changes may require adversarial review.
