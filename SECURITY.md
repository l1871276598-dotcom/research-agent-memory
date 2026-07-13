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
