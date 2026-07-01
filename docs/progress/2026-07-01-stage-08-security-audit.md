# LAOS v0.8 Stage 08 Security Audit

Date: 2026-07-01
Status: PASS for the documented local-only, trusted-operator scope

## Security gates passed

- Candidate creation cannot request active state.
- Active promotion requires Review Gate authority.
- Review actions are restricted to accept, reject, merge, and conflict.
- Review requests cannot cross the LAOS task workspace boundary.
- Candidate and target records are revalidated before review writes.
- Repeated, stale, tampered, rejected, and invalid review operations fail without unintended mutation.
- Restricted and inactive records never enter LAOS search or Context Builder output.
- Project, context, workspace, and confidentiality references are partition-checked at creation and review.
- Source paths reject absolute paths, parent traversal, and symbolic-link escapes.
- Data root and state directory separation, sync-root rejection, and state/database symlink rejection are covered by tests.
- Raw imports use exclusive creation and versioned collision handling instead of overwriting evidence.
- Review/index failures restore files and preserve failure journals.
- LAOS CLI failures return one generic JSON error line, nonzero status, no traceback, and no root/state path disclosure.
- CI permissions are read-only and the local worktree contains no database, PDF, log, cache, real memory, or secret artifact in Git status.

## Threat-model boundary

v0.8 is safe only as a local, trusted-operator CLI. It does not implement operator identity, agent authentication, per-user authorization, or a remote service boundary. The legacy owner-level review entry remains intentionally privileged. Do not expose `src/laos.py`, Import Agent, Review Gate, or the legacy CLI through MCP, HTTP, multi-user automation, or an untrusted process until authentication and capability authorization exist.

## Nonblocking hardening findings

### S08-P2-01: unbounded local input sizes

Manual imports, ChatGPT ZIP parsing, task files, candidate content, and context limits do not have one unified hard maximum. A trusted local operator can therefore cause high memory, disk, or CPU consumption with very large inputs. This is an availability risk, not a confidentiality bypass in the current local-only scope. Add explicit limits before any unattended or remote execution.

### S08-P2-02: arbitrary operator-selected local file import

Import Agent can archive any supported, readable, non-symlink file selected by the local operator. This is required for local import, but would become local-file disclosure if exposed to an untrusted remote caller. Remote exposure is prohibited until path allowlists, explicit consent, and authentication are added.

### S08-P2-03: future prompt-injection boundary

Context Builder returns reviewed memory content as plain text. LAOS currently invokes no model, so this is not an active execution vulnerability. Before connecting Context Builder to an LLM, wrap entries as quoted data with provenance and instruct the consumer not to treat stored content as executable instructions.

### S08-P2-04: CI supply-chain hardening

GitHub Actions use version tags rather than commit-SHA pinning, and no automated secret scanner is configured. Current workflow permissions are read-only. SHA pinning and secret scanning are recommended before public or multi-contributor release.

## Release decision

No new P0 or P1 security defect was found. Stage 08 passes for v0.8 local-only scope. The four P2 items must remain explicit non-goals or be completed before MCP, HTTP, multi-user, autonomous, or unattended operation.
