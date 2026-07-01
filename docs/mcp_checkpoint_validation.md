# MCP checkpoint capability validation

This experiment answers one narrow question:

> Can the existing ChatGPT-to-MCP bridge reliably submit an explicit completed
> exchange to LAOS?

It does **not** assume that MCP can passively read the ChatGPT transcript.

## What the tool records

`laos_capture_checkpoint` accepts caller-submitted values:

- `session_alias`
- `user_message`
- `assistant_response`
- optional stable checkpoint and source conversation/message IDs
- branch and version information when available
- an optional request to force candidate review

LAOS stores the user and assistant messages through the existing Bridge Event
Inbox and Session Projector. The receipt contains content lengths and SHA256
hashes so the submitted text can be compared with the text displayed in
ChatGPT.

The receipt always states:

```json
{
  "capture_mode": "explicit_tool_call",
  "passive_conversation_access": false,
  "assistant_text_independently_observed": false
}
```

These values are architectural facts, not test outcomes.

## One-command trial workflow

Prepare an isolated five-checkpoint trial without enabling any model review:

```bash
python3 tools/mcp_checkpoint_trial.py prepare \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --workspace personal \
  --project laos-checkpoint-test \
  --expected-checkpoints 5
```

This initializes the local store, writes `mcp_checkpoint_trial.json` into the
state directory, and prints the exact ChatGPT instruction for the trial.

Start the MCP server from the saved manifest:

```bash
python3 tools/mcp_checkpoint_trial.py serve \
  --state-dir "$STATE_DIR"
```

After the expected calls, generate the automated portion of the decision report:

```bash
python3 tools/mcp_checkpoint_trial.py report \
  --state-dir "$STATE_DIR"
```

The report intentionally leaves three empirical fields unresolved: whether the
tool was called on every expected turn, whether each assistant hash matches the
final displayed response, and whether the write-confirmation burden is
acceptable. A passing automated report alone cannot promote MCP to a formal
checkpoint channel.

Record the empirical result and make the final bounded classification:

```bash
python3 tools/mcp_checkpoint_trial.py decide \
  --state-dir "$STATE_DIR" \
  --tool-called-every-turn yes \
  --hashes-match yes \
  --confirmation-burden-acceptable yes
```

The decision command accepts MCP only when the automated report and all three
manual evidence checks pass. Its strongest possible classification is
`formal_explicit_checkpoint_channel`. It always records that passive or
lossless conversation capture has not been proven.

## Direct server command

Checkpoint capture is disabled by default. It can also be enabled directly:

```bash
python3 tools/serve_laos.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --enable-checkpoint-capture \
  --checkpoint-workspace personal \
  --checkpoint-project laos-checkpoint-test
```

Do not provide `--model-config` during the first capture test. This isolates the
question of whether ChatGPT can submit and LAOS can persist the exchange. Model
review can be tested separately after capture reliability is established.

## Instruction for the connected ChatGPT client

Use an instruction equivalent to:

```text
After preparing each completed response, call laos_capture_checkpoint once.
Use one stable session_alias throughout this conversation. Pass the current
user message verbatim. Pass assistant_response exactly as it will be shown to
the user. Reuse the same checkpoint_id only when retrying the same exchange.
Do not invent source conversation or message IDs; leave them empty unless the
host exposes real stable IDs. Do not force review during this validation.
```

The instruction itself is part of the experiment. LAOS cannot force ChatGPT to
invoke an MCP tool on every turn.

## Five-turn test

Run five consecutive exchanges in one ChatGPT conversation:

1. A short plain-text question.
2. A Chinese question and Chinese response.
3. A response containing Markdown and a code block.
4. A long response large enough to expose truncation or argument limits.
5. A normal response after refreshing or reopening the ChatGPT conversation.

For each exchange, record:

- whether the tool was called without a reminder
- whether the UI required confirmation
- the checkpoint receipt
- whether the submitted user-message hash matches the actual user message
- whether the submitted assistant-response hash matches the final displayed answer
- whether real source conversation/message IDs were available

Regeneration and edited-message branching should be tested separately. Use a
new checkpoint ID and increment `version`, or use a distinct branch ID.

## Generate the lower-level LAOS report

The original report command remains available:

```bash
python3 tools/checkpoint_validation.py \
  --state-dir "$STATE_DIR" \
  --expected-checkpoints 5
```

The command exits successfully only when the expected number of checkpoints is
present, every checkpoint contains a processed user and assistant message, and
no bridge event failed.

## Decision rule

MCP checkpoint synchronization is acceptable only when all of the following
are true:

1. Every expected turn invoked the tool without repeated prompting.
2. All checkpoints were processed without missing or duplicate session messages.
3. Manually compared hashes show that submitted text matches the displayed text.
4. The confirmation burden is acceptable for the intended workflow.
5. Missing source IDs are understood and accepted.

Even when all five conditions pass, the conclusion is limited to:

> MCP is usable for explicit checkpoints or important decision snapshots.

The experiment can never prove:

- passive access to every ChatGPT message
- automatic capture when the model forgets to call the tool
- independent observation of the final assistant response
- lossless edit, regeneration, deletion, and branch tracking without source IDs

If the project requires a complete transcript with no model cooperation, the
correct upstream remains a browser-side or platform-provided conversation event
source. The existing Bridge Event Inbox and Session Projector can accept that
source without redesign.
