# Live ChatGPT Archive

This optional path archives the current ChatGPT context directly to the Mac without waiting for an account export ZIP.

## Architecture

```text
ChatGPT private Custom GPT action
        ↓ HTTPS + bearer token
Tailscale Funnel
        ↓
127.0.0.1:8765
        ↓
memory_tools.py serve-chatgpt
        ↓
imports/chatgpt/live/YYYY/MM/*.md
```

The receiver binds only to loopback. The HTTPS tunnel is configured separately on the Mac.

Live archives are provisional current-context snapshots. They are kept separate from the canonical files later imported from the official ChatGPT export ZIP.

## Start the local receiver

```bash
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"

mkdir -p "$STATE_DIR"
umask 077
python3.13 -c 'import secrets; print(secrets.token_urlsafe(48))' \
  > "$STATE_DIR/chatgpt_archive.token"

python3.13 src/memory_tools.py serve-chatgpt \
  --root "$DATA_ROOT" \
  --token-file "$STATE_DIR/chatgpt_archive.token"
```

Check locally:

```bash
curl http://127.0.0.1:8765/health
```

## Expose the receiver with Tailscale Funnel

Install and sign in to Tailscale, then run:

```bash
tailscale funnel --bg 8765
tailscale funnel status
```

Use the reported `https://...ts.net` address as the OpenAPI `servers.url` value.

The Mac must be awake, online, connected to Tailscale, and running the local receiver. The endpoint is public, so keep bearer authentication enabled and never publish the token.

## Custom GPT instructions

Create a private GPT and use these instructions:

```text
You are the private Research Agent archive assistant.

When the user says “归档本次”, “archive this conversation”, or otherwise clearly asks to archive the current chat:

1. Call archiveCurrentConversation exactly once.
2. Include only visible user and assistant messages from the current conversation, in their original order.
3. Never include system, developer, tool, hidden, or internal instruction messages.
4. Do not invent omitted messages.
5. Set is_complete to true only when every visible user and assistant message available in the current context was included. Otherwise set it to false.
6. Use a concise title. Leave conversation_key empty unless the user explicitly supplied one; the receiver will derive a stable key.
7. If visible content appears to contain passwords, API keys, access tokens, private keys, or other credentials, replace only the credential value with [REDACTED] and set is_complete to false.
8. After the action succeeds, report the returned status, archive_id, relative_path, message_count, and is_complete.
9. If the action fails, explain that the Mac receiver or tunnel may be offline and do not claim the archive succeeded.
```

A private GPT can be invoked inside an existing ChatGPT conversation with `@`, preserving the current conversation context. Use:

```text
@你的归档GPT 归档本次
```

ChatGPT may show a confirmation prompt before the write action.

## OpenAPI schema

Replace `https://YOUR-MAC.YOUR-TAILNET.ts.net` with the Funnel URL.

```yaml
openapi: 3.1.0
info:
  title: Research Agent Live Archive
  version: 1.0.0
  description: Archive visible user and assistant messages to the owner's Mac.
servers:
  - url: https://YOUR-MAC.YOUR-TAILNET.ts.net
paths:
  /archive:
    post:
      operationId: archiveCurrentConversation
      summary: Archive the current visible ChatGPT conversation context
      description: >
        Use only when the user explicitly asks to archive the current chat.
        Never send system, developer, tool, hidden, or internal messages.
      security:
        - bearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              additionalProperties: false
              required:
                - title
                - messages
              properties:
                conversation_key:
                  type: string
                  maxLength: 128
                  description: Optional stable key. Omit when unavailable.
                title:
                  type: string
                  maxLength: 500
                created_at:
                  type: string
                  format: date-time
                updated_at:
                  type: string
                  format: date-time
                summary:
                  type: string
                  maxLength: 20000
                is_complete:
                  type: boolean
                  default: false
                messages:
                  type: array
                  minItems: 1
                  items:
                    type: object
                    additionalProperties: false
                    required:
                      - role
                      - content
                    properties:
                      role:
                        type: string
                        enum:
                          - user
                          - assistant
                      content:
                        type: string
                        minLength: 1
                      created_at:
                        type: string
                        format: date-time
      responses:
        "200":
          description: Existing archive updated or unchanged
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ArchiveResult"
        "201":
          description: New archive created
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ArchiveResult"
        "400":
          description: Invalid archive payload
        "401":
          description: Missing or invalid bearer token
        "413":
          description: Request is too large
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
  schemas:
    ArchiveResult:
      type: object
      required:
        - status
        - archive_id
        - relative_path
        - message_count
        - content_sha256
        - is_complete
      properties:
        status:
          type: string
          enum:
            - created
            - updated
            - unchanged
        archive_id:
          type: string
        relative_path:
          type: string
        message_count:
          type: integer
        content_sha256:
          type: string
        is_complete:
          type: boolean
```

In the GPT editor, configure API key authentication with the value from:

```text
~/Library/Application Support/ResearchAgent/chatgpt_archive.token
```

Use bearer authentication so the request header is:

```text
Authorization: Bearer <token>
```

## Archive behavior

- Only `user` and `assistant` roles are accepted.
- The receiver rejects hidden/system/tool roles.
- Requests larger than 1 MB are rejected by default.
- The same current conversation seed resolves to the same archive file.
- Repeating identical content returns `unchanged`.
- Changed content updates the same file.
- Title changes do not create a second file.
- Raw live archives are not automatically promoted to formal long-term memory.
- Raw live archives are not added to the formal SQLite memory index.
- Official export ZIP import remains the canonical full-history path.

## Limitations

A GPT Action sends JSON generated from the model's current context; it is not the same as an account-level raw transcript export. Older content outside the current model context may be absent. The `is_complete` field records this distinction.

When the official ZIP arrives, import it normally:

```bash
python3.13 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT"
```
