# Stage 16 — LAOS v0.9 Release Review

Date: 2026-07-04
Decision: PASS for feature-branch push and Draft PR
Release target: v0.9.0
Current repository metadata: v0.9.0-development

## Review scope

Stage 16 reviewed the release-facing surface of the completed Stage 10–15 implementation:

- version and phase-status consistency
- README and user-facing usage instructions
- final JSON CLI example for the lightweight Loop Coordinator
- local validation and release checklist
- local branch and release-operation boundaries

This review does not merge the branch, create a version tag, publish a GitHub Release, deploy a service, or claim that remote CI has passed.

## Version decision

The release target is `v0.9.0`, while repository metadata remains:

```text
v0.9.0-development
```

This is intentional for the Draft PR stage. The branch is release-ready for review, but `v0.9.0` must not be presented as formally released before merge, tag, and GitHub Release confirmation.

`docs/PHASE_STATUS.json` now records:

- `release_readiness: passed`
- `stage_16_v0.9_release: passed`
- all Stage 10–15 implementation and audit gates as passed

## README and usage review

The previous README was a release blocker because it still described v0.8 and incorrectly listed the Agent Registry, Orchestrator, Reflection, Policy Learning, and the lightweight Auto-update Loop as unimplemented.

The README was replaced with an accurate v0.9 guide covering:

- the nine-Agent v0.9 runtime
- candidate-only creation and Review Gate ownership
- deterministic Reflection and Policy artifacts
- the fixed three-independent-evidence threshold
- workspace and project partition isolation
- the lightweight Coordinator flow
- local state and data-directory separation
- supported CLI entry points
- explicit non-goals and trusted-operator security limits
- the release sequence from Stage 16 through Draft PR, CI, merge, tag, and GitHub Release

No runtime behavior or public data contract was changed during Stage 16.

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

The example matches the implemented Coordinator contract:

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

The README explicitly states that the Coordinator consumes completed-task evidence; it does not execute the represented task. It also explains that repeating the identical request reuses the same Loop run and does not count as independent evidence.

## Release checklist

| Gate | Result |
|---|---|
| v0.9 target and development metadata are distinguished | PASS |
| Phase status marks Stage 16 and release readiness passed | PASS |
| README reflects the nine-Agent v0.9 runtime | PASS |
| README documents candidate-only and Review Gate boundaries | PASS |
| README documents local-only trusted-operator limitations | PASS |
| Final `loop.coordinate` example matches the runtime input contract | PASS |
| Three-independent-evidence behavior is documented | PASS |
| Workspace/project partition behavior is documented | PASS |
| No automatic approval, acceptance, activation, retry, or watcher is claimed | PASS |
| Stage 15 Critical findings | 0 |
| Stage 15 Major findings | 5 fixed |
| Full local regression | PASS |
| Compile validation | PASS |
| Git whitespace checks | PASS |
| Merge, tag, and formal release remain separately gated | PASS |

## Verification evidence

Final Stage 16 local validation executed after the release-document corrections:

```text
364 tests passed
compileall passed
git diff --check passed
git diff --cached --check passed
```

The test suite includes the v0.9 Registry and CLI routes, Loop idempotency, structured Reflection, Policy reconstruction, fixed evidence thresholds, two-phase candidate recovery, workspace/project isolation, tamper rejection, and Review Gate ownership.

## Release operation decision

The local v0.9 implementation and release-facing documentation are suitable for:

1. one focused Stage 16 documentation commit;
2. pushing `feat/laos-v0.9-loop-learning` to the matching origin branch;
3. creating a Draft PR targeting `main`.

The following remain blocked on later explicit gates:

- merging the PR
- changing repository metadata from development to a formal released version, if desired
- creating tag `v0.9.0`
- publishing a GitHub Release
- deployment or unattended service exposure

Remote GitHub Actions must be observed after push. Until a remote run completes successfully, only local validation may be reported as passed.
