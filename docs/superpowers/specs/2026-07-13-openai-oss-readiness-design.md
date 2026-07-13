# OpenAI OSS Application Readiness Design

## Goal

Make the public LAOS repository understandable, trustworthy, and application-ready for OpenAI's Codex for Open Source program without changing runtime behavior or overstating adoption.

## Scope

### Public GitHub metadata

- Set the public profile name to `林森`.
- Set the public profile bio to `Maintainer of LAOS · Local-first, auditable memory infrastructure for AI agents.`
- Set the repository description to `Local-first, auditable, human-governed memory and learning layer for AI agents.`
- Add these repository topics: `ai-agents`, `agent-memory`, `local-first`, `human-in-the-loop`, `mcp`, `codex`, `python`, and `obsidian`.
- Enable GitHub private vulnerability reporting and verify that the repository's direct private report form is available at `https://github.com/l1871276598-dotcom/research-agent-memory/security/advisories/new`.

Every public mutation must be read back from GitHub after it is applied. No star, fork, download, user, or community claim may be added unless it is independently verifiable.

### Repository documentation

Modify `README.md` to add a compact English entry point before the existing Chinese documentation:

- one-paragraph product definition;
- a short "Why LAOS" section covering local data ownership, human review, auditability, and model independence;
- a capability summary tied to the current v0.10.0 release;
- a reproducible local demonstration using existing commands;
- links to contribution and security policies.

Create `CONTRIBUTING.md` with the supported scope, Python/macOS setup, test command, pull-request expectations, and privacy constraints.

Create `SECURITY.md` with supported-version scope, a direct link to GitHub's private vulnerability reporting form, response expectations that do not promise fixed deadlines, and the repository's local-trusted-operator security boundary, including the absence of general multi-user authentication or authorization.

The reproducible demonstration stays in `README.md`; its `memory.py index` step is described as incremental synchronization and verification, not a full rebuild or a prerequisite for acceptance or search. No example application, new dependency, or additional documentation hierarchy is required.

The existing README verification and release-history footer is aligned with the delivered repository state: `compileall` covers `src tests tools`; current branch facts are verified by commands instead of frozen HEAD or test-count claims; the v0.9.0 flow and Stage 17 are explicitly historical; and the v0.10.0 release record remains linked as the current release evidence.

## Non-goals

- No LAOS runtime, schema, CLI, test, or workflow behavior changes.
- No documentation site, GUI, badge collection, package publication, issue seeding, or synthetic community activity.
- No claim that clone traffic proves independent adoption.
- No metadata mutation, commit, push, or Draft pull request without one explicit user authorization covering all four actions after the final diff and verification evidence are presented. Merge remains a separate explicit decision.
- No attempt to manufacture external users, citations, stars, forks, or testimonials.

## Verification

Before requesting publication approval:

1. Run the documented demonstration in temporary data and state directories.
2. Run `python3 -m unittest discover -s tests -v` and require zero failures.
3. Run `python3 -m compileall -q src tests tools`.
4. Run `git diff --check` for tracked changes and `git diff --no-index --check /dev/null <file>` for every untracked planned file.
5. Inspect the tracked diff and explicit no-index diffs for all untracked planned files for unsupported claims, private paths, secrets, stale version facts, and out-of-scope changes.

After public metadata is changed, query the GitHub API and inspect the public page to confirm the exact name, bio, description, and topics. Query the repository API to confirm private vulnerability reporting remains enabled.

## Delivery sequence

1. Prepare and verify the documentation changes in the isolated `docs/openai-oss-readiness` worktree.
2. Present the final diff and verification evidence and obtain explicit authorization covering metadata mutation, commit, push, and opening a Draft pull request.
3. Apply and verify the profile and repository metadata, including private vulnerability reporting and its direct report form.
4. Under the same explicit authorization, commit and push the documentation branch and open a Draft pull request.
5. Check GitHub Actions and report any remaining work; merging remains a separate explicit user decision.
