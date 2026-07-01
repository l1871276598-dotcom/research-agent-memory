# Model backends

LAOS core code does not depend directly on Codex. Model-specific authentication,
SDKs, HTTP formats, and provider settings are isolated behind `ModelBackend`.

## Built-in backends

- `codex`: default review backend using the local Codex CLI and existing ChatGPT login.
- `openai_compatible`: configurable HTTP backend for OpenAI-compatible chat APIs,
  including compatible OpenAI, Ollama, LM Studio, vLLM, or gateway endpoints.

## Configuration

Pass a JSON file to the LAOS task CLI:

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --model-config config/model_backend.example.json \
  --task-file task.json
```

Example:

```json
{
  "backend": "openai_compatible",
  "options": {
    "base_url": "http://localhost:11434/v1",
    "model": "replace-with-model-name",
    "api_key_env": "LAOS_MODEL_API_KEY",
    "timeout": 120
  }
}
```

API keys should be supplied through environment variables or trusted runtime
injection, not committed to repository configuration.

## Adding another provider

Implement `ModelBackend.complete()` and/or `ModelBackend.review()`, then register
the factory with `ModelBackendRegistry`. Anthropic-, Gemini-, or other
provider-specific adapters can therefore be added without changing MemoryCore,
ReviewGate, ConversationReviewService, AgentRuntime, or persistence code.

A backend used for automatic memory extraction must implement `review(messages)`
and return the exact LAOS review JSON contract. A backend used for the main agent
loop must implement `complete(messages, tools=...)` and return:

```json
{"content": "...", "tool_calls": []}
```
