# Browser bridge ingestion

The browser bridge captures ChatGPT web conversations without requiring the GPT
API. LAOS stores raw finalized messages first; semantic review is optional and
uses the configured model backend only after persistence succeeds.

## Data flow

```text
browser bridge
→ authenticated localhost event inbox
→ idempotent session projector
→ SQLite session history
→ optional threshold/checkpoint review
→ Memory and Procedure candidates
→ human Review Gate
```

The bridge never writes active memory directly.

## Start the local bridge server

Set a random local token with at least 16 characters:

```bash
export LAOS_BRIDGE_TOKEN="replace-with-a-long-random-token"
python3 tools/bridge_server.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --scope-config config/bridge_scope.example.json
```

The server listens only on `127.0.0.1:8765`.

To enable automatic semantic review, also provide a model configuration:

```bash
python3 tools/bridge_server.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --scope-config config/bridge_scope.example.json \
  --model-config config/model_backend.example.json
```

Without `--model-config`, capture, deduplication, session storage, branching,
and search remain fully operational. No Codex or model API is required.

## Event endpoint

Send one event to `POST /events` with header:

```text
X-LAOS-Bridge-Token: <local token>
Content-Type: application/json
```

Final user message example:

```json
{
  "event_id": "stable-event-id-user-1",
  "event_type": "message",
  "source": "chatgpt-web",
  "account_id": "anonymized-local-account-id",
  "conversation_id": "conversation-id",
  "branch_id": "main",
  "message_id": "user-message-id",
  "parent_message_id": null,
  "version": 1,
  "role": "user",
  "content": "Use minimal code changes.",
  "is_final": true,
  "metadata": {
    "conversation_title": "LAOS development"
  }
}
```

Assistant event example:

```json
{
  "event_id": "stable-event-id-assistant-1",
  "event_type": "message",
  "source": "chatgpt-web",
  "account_id": "anonymized-local-account-id",
  "conversation_id": "conversation-id",
  "branch_id": "main",
  "message_id": "assistant-message-id",
  "parent_message_id": "user-message-id",
  "version": 1,
  "role": "assistant",
  "content": "Understood.",
  "is_final": true,
  "metadata": {}
}
```

A checkpoint forces review of the currently persisted branch without adding a
fake conversation turn:

```json
{
  "event_id": "checkpoint-conversation-id-main-1",
  "event_type": "checkpoint",
  "source": "chatgpt-web",
  "account_id": "anonymized-local-account-id",
  "conversation_id": "conversation-id",
  "branch_id": "main",
  "metadata": {}
}
```

## Bridge requirements

The browser bridge should:

1. Use stable `event_id`, `message_id`, `conversation_id`, and `branch_id` values.
2. Send streaming assistant content with `is_final=false` using the same event ID.
3. Send the completed content once with `is_final=true`.
4. Create a new `version` or branch for edited/regenerated messages.
5. Keep a local outbound queue until LAOS acknowledges the event.
6. Use an anonymized account identifier rather than an email address.
7. Avoid capturing temporary or disabled conversations unless explicitly enabled.

## Guarantees

- Repeated delivery is idempotent.
- Finalized event content is immutable.
- Child messages wait for known parent messages to be processed.
- Separate response branches become separate LAOS sessions.
- Interrupted projections can be requeued safely without duplicate messages.
- Assistant-generated statements are not treated as evidence of user facts.
- `restricted` sessions are saved locally but skipped by external model review.
- Long-term memory and Procedures remain candidates until human approval.

Deleting a conversation from the ChatGPT website does not automatically delete
the independent LAOS copy or active memories. Deletion and retention policy must
be handled explicitly in LAOS.
