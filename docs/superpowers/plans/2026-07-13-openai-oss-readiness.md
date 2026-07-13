# OpenAI OSS Application Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LAOS's public GitHub presence understandable, trustworthy, and ready for a Codex for Open Source application without changing runtime behavior or overstating adoption.

**Architecture:** Keep the change documentation-only: one bilingual README entry point plus two standard community-health files. Apply profile and repository metadata through GitHub's API, then verify the exact public values through both the API and a read-only Computer Use inspection. Preserve one explicit authorization gate covering metadata mutation, commit, push, and opening a Draft pull request; merge remains a separate decision.

**Tech Stack:** Markdown, existing Python 3.11 CLI, Git, GitHub CLI/API, GitHub Actions, Computer Use for public-page verification.

---

### Task 1: Add the English README entry point and reproducible demo

**Files:**
- Modify: `README.md:1-30`
- Verify: existing Python test suite under `tests/`

- [ ] **Step 1: Reconfirm public release facts before editing**

Run:

```bash
gh release view v0.10.0 -R l1871276598-dotcom/research-agent-memory \
  --json tagName,isDraft,isPrerelease,publishedAt,url
gh repo view l1871276598-dotcom/research-agent-memory \
  --json visibility,licenseInfo,defaultBranchRef,pushedAt
```

Expected: `v0.10.0` is published, not draft or prerelease; the repository is `PUBLIC`, MIT licensed, and uses `main`.

- [ ] **Step 2: Insert this exact English block after the H1 and before the existing Chinese introduction**

```markdown
LAOS is a local-first, auditable, human-governed memory and learning layer for AI agents. It keeps structured memory, project context, task evidence, reflections, and reusable principles in user-controlled files with rebuildable indexes, so GPT, Codex, Claude, local models, and other agents can reuse knowledge without bypassing human review.

**Current release:** [v0.10.0](https://github.com/l1871276598-dotcom/research-agent-memory/releases/tag/v0.10.0) · Python 3.11+ · macOS acceptance target · [MIT](LICENSE)

[Reproducible local demo](#reproducible-local-demo) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Why LAOS

- **Local ownership:** authoritative memory stays in user-controlled files, while derived indexes remain rebuildable.
- **Human authority:** agents create candidates, and only an explicit Review Gate decision can accept them.
- **Auditable, deterministic lineage:** memory and learning artifacts retain traceable source evidence and deterministic processing paths.
- **Model independence:** GPT, Codex, Claude, local models, and other agents can use the same governed knowledge layer.
- **Fail-closed isolation:** workspace, project, confidentiality, and restricted-data boundaries reject mismatches or exclude data by default.

## Current capabilities (v0.10.0)

v0.10.0 includes Memory Core, a unified 12-agent JSON CLI, a deterministic learning chain, controlled conversation review, MCP checkpoint tooling, atomic vault promotion, crash-replay convergence, and GitHub Actions. It provides no autonomous approval, passive browser capture, multi-user authorization, or production deployment outside the local trusted-operator boundary.

## Reproducible local demo

Run this from the repository root with Python 3.11 or later. The demo keeps both authoritative data and derived state in fresh temporary directories, contacts no model API, and removes its files when the demo block ends. The create response includes `"requires_review":true`; the candidate becomes searchable after the explicit `memory.review` acceptance. The subsequent `memory.py index` command incrementally synchronizes the derived index; in this sequence it verifies that the accepted record is already current and is not a prerequisite for acceptance or search.

```bash
(
set -euo pipefail

DEMO_DIR="$(mktemp -d)"
DATA_ROOT="$DEMO_DIR/data"
STATE_DIR="$DEMO_DIR/state"
trap 'rm -rf -- "$DEMO_DIR"' EXIT

python3 src/memory.py init --root "$DATA_ROOT"
python3 src/memory.py db-init \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"

CREATE_RESULT="$(
  python3 src/laos.py \
    --root "$DATA_ROOT" \
    --state-dir "$STATE_DIR" \
    --task-json '{"type":"memory.create","input":{"type":"principle","title":"Evidence before claims","scope":"global","workspace":"personal","confidentiality":"personal","source":"manual:user_confirmed","confidence":"confirmed","content":"Require evidence before making claims."}}'
)"
printf '%s\n' "$CREATE_RESULT"

CANDIDATE_ID="$(
  printf '%s\n' "$CREATE_RESULT" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["output"]["candidate_id"])'
)"

python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json "{\"type\":\"memory.review\",\"workspace\":\"personal\",\"input\":{\"action\":\"accept\",\"candidate_id\":\"$CANDIDATE_ID\"}}"

python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"

python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"memory.search","input":{"query":"Evidence before claims","workspace":"personal"}}'
)
```

## 中文说明
```

Keep the existing Chinese introduction and all later sections immediately after `## 中文说明`.

- [ ] **Step 3: Remove the stale moving-branch assertion from the Chinese release paragraph**

Replace the sentence fragment that claims a specific current `main` HEAD with this stable release statement:

```markdown
`v0.10.0` 由发布 PR #39 squash merge 到 `main`（合并提交 `77b3c6d`，`v0.10.0` tag 打在该提交上）。发布门禁记录为 **879 项通过 / 0 失败**（v0.9.0 基线为 508，四条特性流累积 508 → 787 → 866 → 879）；四轮外部发布评审收敛至 GO，`release_readiness` 为 `passed`，`deployment` 为 `not_applicable`（本地优先纯源码发布）。发布详情见 [v0.10.0 发布准备记录](docs/progress/2026-07-11-v0.10.0-release-preparation.md)。
```

- [ ] **Step 4: Align the README verification and release-history footer with the delivered release state**

Replace the README content from `## 本地验证` through the v0.10.0 release-record bullet under `## 文档` with the exact text below. Preserve the remaining documentation bullets beginning with `- MCP checkpoint` immediately afterward.

````markdown
## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests tools
git diff --check
git diff --cached --check
```

`v0.9.0` 的历史 Stage 17 macOS 集成与正式发布记录分别见 [Stage 17 最终整合审查](docs/progress/2026-07-05-stage-17-macos-final-integration-review.md)和 [v0.9.0 发布准备记录](docs/progress/2026-07-06-v0.9.0-release-preparation.md)。当前正式版本 `v0.10.0` 的记录见 [v0.10.0 发布准备记录](docs/progress/2026-07-11-v0.10.0-release-preparation.md)，其发布门禁结果为 **879 项通过 / 0 失败**。当前分支状态必须通过运行上述命令验证，不在说明文字中固定 SHA 或测试数量。

## 历史 v0.9.0 发布门禁

```text
Historical v0.9.0 release flow
→ Stage 17 local macOS integration review
→ commit documentation alignment
→ push feature branch
→ Draft PR macOS CI
→ human review
→ merge confirmation
→ v0.9.0 release preparation
→ v0.9.0 tag / GitHub Release confirmation
→ released
```

以上仅记录历史 `v0.9.0` 发布流程。本地验收、Draft PR、远程 CI、合并、tag 和正式 release 是不同 Gate；`v0.9.0` 的全部源码发布 Gate 已完成。后续代码变更继续按独立 PR 与 CI 门禁处理。

## 文档

- 当前阶段状态：`docs/PHASE_STATUS.json`
- 当前路线图：`docs/ROADMAP.md`
- macOS-first 决策：`docs/decisions/2026-07-05-macos-first-delivery-scope.md`
- v0.9 架构与安全审查：`docs/progress/2026-07-04-stage-15-v09-architecture-security-audit.md`
- v0.9 初始发布审查：`docs/progress/2026-07-04-stage-16-v09-release-review.md`
- v0.9.0 / Stage 17 历史 macOS 最终整合审查：`docs/progress/2026-07-05-stage-17-macos-final-integration-review.md`
- v0.9.0 正式发布记录：`docs/progress/2026-07-06-v0.9.0-release-preparation.md`
- v0.10.0 正式发布记录：`docs/progress/2026-07-11-v0.10.0-release-preparation.md`
````

This footer must not describe the current branch with a frozen HEAD SHA or the historical 508-test count. The only 508 reference that remains elsewhere in the README is explicitly identified as the historical v0.9.0 baseline or Stage 17 result.

- [ ] **Step 5: Run the existing suite after the README modification**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: `Ran 884 tests` and `OK` on the current baseline.

### Task 2: Add contribution and security policies

**Files:**
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Verify: existing Python test suite under `tests/`

- [ ] **Step 1: Create `CONTRIBUTING.md` with this exact content**

```markdown
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
```

- [ ] **Step 2: Create `SECURITY.md` with this exact content**

```markdown
# Security Policy

## Supported versions

The current release line and current `main` branch receive security fixes.

| Version | Supported |
| --- | --- |
| `v0.10.x` | Yes |
| Current `main` | Yes |
| `v0.9.x` and older | No |

## Reporting a vulnerability

Do not disclose an unpatched vulnerability publicly in an issue, pull request,
discussion, or comment.

Report suspected vulnerabilities through GitHub's
[private vulnerability reporting form](https://github.com/l1871276598-dotcom/research-agent-memory/security/advisories/new).

Include:

- the affected version or commit;
- the affected security boundary;
- reproduction steps;
- expected and observed behavior; and
- a minimal proof of concept without real data or credentials.

The maintainer will acknowledge the report after it has been reviewed and will
coordinate disclosure after the impact and remediation are understood. No
fixed response or release deadline is promised.

## Security boundary

LAOS is intended for a trusted local operator on macOS. Its MCP, HTTP, bridge,
and CLI interfaces do not provide general multi-user authentication or
authorization. Do not expose them to public networks or untrusted users.

Authoritative memories remain in user-controlled files. Memory becomes active
only through an explicit Review Gate decision, and restricted data is excluded
by default. Never commit real memories, databases, unpublished documents,
PDFs, logs, credentials, tokens, or other private data.
```

- [ ] **Step 3: Confirm community files and links resolve locally**

Run:

```bash
test -f CONTRIBUTING.md
test -f SECURITY.md
rg -n '\[Contributing\]\(CONTRIBUTING\.md\)|\[Security\]\(SECURITY\.md\)' README.md
```

Expected: both files exist and README reports both links.

- [ ] **Step 4: Run the existing suite after adding the two policy files**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: `Ran 884 tests` and `OK` unless a concurrent main update legitimately changes the collected count; any failure blocks publication.

### Task 3: Verify the documented demo and documentation claims

**Files:**
- Verify: `README.md`
- Verify: `CONTRIBUTING.md`
- Verify: `SECURITY.md`
- Verify: `docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md`
- Verify: `docs/superpowers/plans/2026-07-13-openai-oss-readiness.md`

- [ ] **Step 1: Copy the README demo commands into a fresh shell and run them unchanged**

Expected:

- `memory.create` returns a non-empty `output.candidate_id` and `requires_review: true`;
- `memory.review` returns success for the same candidate in the `personal` workspace;
- `memory.py index` performs incremental synchronization and reports the accepted record already current in this sequence;
- `memory.search` returns the accepted `Evidence before claims` principle;
- no model API or external service is contacted.

- [ ] **Step 2: Run complete repository verification**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests tools
git diff --check
git diff --no-index --check /dev/null CONTRIBUTING.md
test "$?" -eq 1
git diff --no-index --check /dev/null SECURITY.md
test "$?" -eq 1
git diff --no-index --check /dev/null \
  docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md
test "$?" -eq 1
git diff --no-index --check /dev/null \
  docs/superpowers/plans/2026-07-13-openai-oss-readiness.md
test "$?" -eq 1
```

Expected: all tests pass and compilation exits 0. `git diff --check` exits 0 for the tracked README change. Each `git diff --no-index --check /dev/null <file>` command prints nothing and exits 1 because the clean new file differs from `/dev/null`; the immediately following `test` converts that expected result into success. An exit greater than 1 or any whitespace-error output fails the check.

- [ ] **Step 3: Scan for unsupported claims and private local paths**

Run:

```bash
rg -n -i 'wide[l]y used|production[-]ready|500[ ]unique|3[,]778|thousands[ ]of users|thousands[ ]of downloads' \
  README.md CONTRIBUTING.md SECURITY.md \
  docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md \
  docs/superpowers/plans/2026-07-13-openai-oss-readiness.md
rg -n '/Users[/]|/home[/]' \
  README.md CONTRIBUTING.md SECURITY.md \
  docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md \
  docs/superpowers/plans/2026-07-13-openai-oss-readiness.md
```

Expected: both commands return no matches.

- [ ] **Step 4: Review the bounded diff**

Run:

```bash
git status --short
git diff -- README.md

git diff --no-index -- /dev/null CONTRIBUTING.md
test "$?" -eq 1
git diff --no-index -- /dev/null SECURITY.md
test "$?" -eq 1
git diff --no-index -- /dev/null \
  docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md
test "$?" -eq 1
git diff --no-index -- /dev/null \
  docs/superpowers/plans/2026-07-13-openai-oss-readiness.md
test "$?" -eq 1
```

Expected: `git status --short` lists exactly the five planned documentation files. The tracked diff displays README, and each no-index content diff displays one complete untracked file. Exit 1 from each no-index content diff is the expected “files differ” result for a new file; exit 0 would mean no content and any exit greater than 1 is an error. Review all five displayed diffs and confirm that no runtime, test, workflow, data, or generated file is modified.

### Task 4: Obtain the explicit repository publication gate

**Files:**
- Review only: the five files listed in Task 3

- [ ] **Step 1: Present the final diff and fresh verification evidence to the user**

Report the modified files, exact implemented content, commands run, results, and unfinished content as required by `AGENTS.md`.

- [ ] **Step 2: Wait for explicit authorization covering all publication actions**

Do not mutate GitHub metadata, commit, push, or open a Draft pull request until the user explicitly authorizes all four actions after seeing the final five-file diff and fresh verification evidence. One authorization may cover the four named actions; it does not authorize merge. If the user requests changes, apply them and repeat Task 3 in full.

### Task 5: Apply and verify public GitHub metadata

**Files:**
- External profile: `https://github.com/l1871276598-dotcom`
- External repository: `https://github.com/l1871276598-dotcom/research-agent-memory`

- [ ] **Step 1: Update the public GitHub profile after the publication gate**

Run:

```bash
gh api --method PATCH user \
  -f name='林森' \
  -f bio='Maintainer of LAOS · Local-first, auditable memory infrastructure for AI agents.'
```

Expected: the response contains the exact name and bio.

- [ ] **Step 2: Update repository description and topics**

Run:

```bash
gh repo edit l1871276598-dotcom/research-agent-memory \
  --description 'Local-first, auditable, human-governed memory and learning layer for AI agents.' \
  --add-topic 'ai-agents,agent-memory,local-first,human-in-the-loop,mcp,codex,python,obsidian'
```

Expected: command exits 0.

- [ ] **Step 3: Read the exact values back through GitHub's API**

Run:

```bash
gh api user --jq '{name,bio}'
gh repo view l1871276598-dotcom/research-agent-memory \
  --json description,repositoryTopics,url,visibility
```

Expected: exact approved name, bio, description, all eight topics, repository URL, and `PUBLIC` visibility.

- [ ] **Step 4: Verify private vulnerability reporting remains enabled**

Verify that private vulnerability reporting remains enabled:

```bash
gh api repos/l1871276598-dotcom/research-agent-memory/private-vulnerability-reporting \
  --jq '{enabled}'
```

Expected: the response is `{"enabled":true}`. Do not disable or reconfigure private vulnerability reporting, and do not replace the direct form in `SECURITY.md` with a public-contact fallback.

- [ ] **Step 5: Use Computer Use for a read-only public-page verification**

Open the GitHub profile, repository, and direct private vulnerability report form at `https://github.com/l1871276598-dotcom/research-agent-memory/security/advisories/new` in Chrome with Computer Use. Confirm that the rendered profile name and bio are visible, the repository description and topics render correctly, the private report form is available, and no private data is exposed. Do not edit through the UI.

### Task 6: Commit, push, and open the documentation pull request

**Files:**
- Stage: the five documentation files listed in Task 3

- [ ] **Step 1: Commit only under the explicit multi-action authorization from Task 4**

Run:

```bash
git add README.md CONTRIBUTING.md SECURITY.md \
  docs/superpowers/specs/2026-07-13-openai-oss-readiness-design.md \
  docs/superpowers/plans/2026-07-13-openai-oss-readiness.md
git commit -m 'docs: prepare LAOS for open-source maintainer applications'
```

Expected: one commit containing only the five planned files.

- [ ] **Step 2: Push the isolated branch**

Run:

```bash
git push -u origin docs/openai-oss-readiness
```

Expected: the remote branch is created and tracks `origin/docs/openai-oss-readiness`.

- [ ] **Step 3: Open a Draft pull request only under the same explicit authorization**

Run:

```bash
gh pr create \
  --repo l1871276598-dotcom/research-agent-memory \
  --base main \
  --head docs/openai-oss-readiness \
  --draft \
  --title 'docs: make LAOS open-source maintainer application-ready' \
  --body $'## Summary\n- add a concise English LAOS entry point and reproducible local demo\n- add contribution and security policies\n- remove a stale moving-branch assertion\n\n## Verification\n- full unittest suite\n- compileall\n- documented demo\n- git diff --check\n\nNo runtime behavior, dependencies, or adoption claims changed.'
```

Expected: GitHub returns the new draft pull-request URL.

- [ ] **Step 4: Verify the pull-request scope and checks**

Run:

```bash
gh pr view --repo l1871276598-dotcom/research-agent-memory \
  --json number,url,isDraft,files,commits,statusCheckRollup
gh pr checks --repo l1871276598-dotcom/research-agent-memory --watch
```

Expected: the PR is a draft, contains only the five planned files, and all required checks pass. A failing check blocks any merge recommendation.

- [ ] **Step 5: Stop before merge**

Report the PR URL and CI result. Do not mark ready for review or merge without a separate explicit user decision.
