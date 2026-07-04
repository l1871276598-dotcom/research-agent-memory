# Stage 16 — LAOS v0.9 Release Review

Date: 2026-07-04
Decision: PASS for feature-branch push and Draft PR review
Release target: v0.9.0
Current repository metadata: v0.9.0-development
Integrated base: origin/main at 9680adb37e3843b198c7b779f0d3fc75b8d8d497

## Review scope

Stage 16 reviewed and integrated the release-facing surface of the completed Stage 10–15 learning pipeline with the latest `origin/main` runtime additions:

- version and phase-status consistency
- README and user-facing usage instructions
- final JSON CLI example for the lightweight Loop Coordinator
- deterministic learning agents and conversation-review agents in one runtime registry
- ModelBackend, Bridge, MCP checkpoint, session, procedure, and automatic-update-loop documentation
- local validation and release checklist
- feature-branch, Draft PR, merge, tag, and release boundaries

This review does not merge the PR, create a version tag, publish a GitHub Release, deploy a service, or claim that remote CI has passed.

## Version decision

The release target is `v0.9.0`, while repository metadata remains:

```text
v0.9.0-development
```

This is intentional for the Draft PR stage. The branch is release-ready for review, but `v0.9.0` must not be presented as formally released before merge, tag, and GitHub Release confirmation.

`docs/PHASE_STATUS.json` records:

- `release_readiness: passed`
- `stage_16_v0.9_release: passed`
- `v0.9_runtime_agent_count: 11`
- all Stage 10–15 implementation and audit gates as passed
- MCP checkpoint service as implemented
- passive browser capture as not implemented
- real ChatGPT MCP acceptance as blocked by current account capability

## origin/main integration

GitHub initially reported the Draft PR as non-mergeable. The feature branch therefore integrated the latest `origin/main` with a controlled no-commit merge.

Four files conflicted:

- `README.md`
- `docs/PHASE_STATUS.json`
- `src/agents/reflection.py`
- `src/laos.py`

The conflicts represented parallel, compatible capabilities rather than mutually exclusive implementations:

- the feature branch provided deterministic `loop.reflect`, Policy, low-risk candidate generation, and `loop.coordinate`;
- `origin/main` provided conversation review, `reflection.record`, ModelBackend, Bridge/MCP checkpoint, and runtime services.

The resolution preserved both paths:

- `src/agents/reflection.py` exports both `ReflectionAgent` and `ConversationReviewAgent`;
- `src/laos.py` constructs both learning paths and shares the same candidate-only Memory Core;
- `src/agents/registry-v0.9.yaml` contains 11 exact-match Agents;
- Review Gate ownership and workspace/project isolation remain unchanged.

No automatic candidate acceptance, policy activation, background retry, or unattended promotion was introduced by the merge resolution.

## README and usage review

The README now covers the integrated v0.9 surface:

- the unified 11-Agent JSON CLI
- candidate-only creation and Review Gate ownership
- deterministic Reflection and Policy artifacts
- the fixed three-independent-evidence threshold
- workspace and project partition isolation
- the lightweight Coordinator flow
- conversation review and reflection recording
- ModelBackend selection
- Bridge and MCP checkpoint limitations
- local state and data-directory separation
- explicit non-goals and trusted-operator security limits
- the release sequence from Stage 16 through Draft PR, CI, merge, tag, and GitHub Release

## Final CLI example

The final user-facing example uses a UTF-8 task file and the canonical JSON CLI:

```bash
cat > /tmp/laos-loop-task.json <<'JSON'
{
  "type": "loop.coordinate",
  "input": {
    "task": "验证迁移流程",
    "result": "迁移在写入前因版本不匹配而停止",
    "outcome": "fail",
    "error": "目标版本不匹配",
    "reflection": "预检查成功阻止了不安全写入",
    "root_cause": "流程缺少目标版本预检查",
    "next_change": "迁移前必须验证目标版本",
    "workspace": "personal"
  }
}
JSON

python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-file /tmp/laos-loop-task.json
```

Required inputs:

- `task`
- `result`
- `outcome`
- `workspace`

Optional inputs:

- `error`
- `reflection`
- `root_cause`
- `next_change`
- `project`

The Coordinator consumes completed-task evidence; it does not execute the represented task. Repeating the identical request reuses the same Loop run and does not count as independent evidence.

## Release checklist

| Gate | Result |
|---|---|
| v0.9 target and development metadata are distinguished | PASS |
| Latest `origin/main` integrated | PASS |
| Merge conflicts resolved without dropping either runtime path | PASS |
| Phase status marks Stage 16 and release readiness passed | PASS |
| README reflects the unified 11-Agent runtime | PASS |
| README documents candidate-only and Review Gate boundaries | PASS |
| README documents local trusted-operator limitations | PASS |
| MCP checkpoint is not described as passive capture | PASS |
| Final `loop.coordinate` example matches the runtime input contract | PASS |
| Three-independent-evidence behavior is documented | PASS |
| Workspace/project partition behavior is documented | PASS |
| No automatic approval, acceptance, activation, retry, or watcher is claimed | PASS |
| Stage 15 Critical findings | 0 |
| Stage 15 Major findings | 5 fixed |
| Full integrated local regression | PASS |
| Compile validation | PASS |
| Git whitespace checks | PASS |
| Merge, tag, and formal release remain separately gated | PASS |

## Verification evidence

Final integrated local validation after conflict resolution:

```text
475 tests passed
compileall passed
git diff --check passed
git diff --cached --check passed
```

The integrated suite covers:

- v0.9 Registry and CLI routes
- deterministic Loop idempotency, Reflection, Policy, and candidate generation
- conversation review and reflection recording
- ModelBackend injection
- Bridge inbox, projection, recovery, and checkpoint behavior
- MCP checkpoint limitation reporting
- automatic update Loop baseline/review/verification/comparison
- workspace/project isolation, tamper rejection, and Review Gate ownership

The test run emitted existing `ResourceWarning` messages for unclosed SQLite connections in a project-governance test path. The suite still completed with zero failures. This is recorded as a nonblocking Minor quality issue and is not attributed to the merge resolution.

## Release operation decision

The integrated local branch is suitable for:

1. committing the controlled `origin/main` merge resolution;
2. pushing `feat/laos-v0.9-loop-learning` to its matching origin branch;
3. allowing the existing Draft PR to rerun remote CI.

The following remain blocked on later explicit gates:

- marking the Draft PR ready for review
- merging the PR
- changing repository metadata to a formal released version, if desired
- creating tag `v0.9.0`
- publishing a GitHub Release
- deployment or unattended service exposure

Remote GitHub Actions must be observed after push. Until all remote runs complete successfully, only local validation may be reported as passed.
