# Decision: MCP checkpoint channel

Date: 2026-07-01
Status: decided for current deployment

## Decision

Do not adopt MCP checkpoint as the formal automatic stage-snapshot channel for
the current ChatGPT Plus deployment.

Retain the implementation as an optional experimental explicit checkpoint path
for environments that provide full MCP write actions and a reviewed remote
connection.

## Evidence

- ChatGPT connects to remote MCP servers, not directly to local stdio servers.
- LAOS now provides a loopback Streamable HTTP endpoint suitable for a secure
  tunnel, but the current account still needs write-capable MCP access.
- The active ChatGPT Plus plan does not provide the full MCP write path required
  by `laos_capture_checkpoint` under the product availability documented on the
  decision date.
- Therefore the required five-turn write test cannot be executed in the current
  deployment, and no reliability claim can be made.
- Even in an eligible workspace, model-initiated MCP calls cannot prove passive
  or lossless transcript capture.

## Architecture consequence

- MCP remains suitable for optional explicit snapshots, reviewed actions, and
  future Business or Enterprise/Edu deployments.
- MCP must not be described as automatic conversation ingestion.
- The formal automatic ChatGPT conversation source should be a browser-side or
  platform-provided event source feeding the existing Bridge Event Inbox.
- Bridge Event Inbox, Session Projector, idempotency, recovery, and Review Gate
  remain reusable and require no redesign.

## Revisit conditions

Reopen this decision only when all of the following are true:

1. The active ChatGPT workspace supports full MCP write actions.
2. A secure remote endpoint or Secure MCP Tunnel is connected.
3. Five expected tool calls complete without omission.
4. Submitted assistant hashes match the final displayed responses.
5. Confirmation burden is acceptable.

The strongest permitted future classification remains
`formal_explicit_checkpoint_channel`, never passive or lossless transcript
capture.
