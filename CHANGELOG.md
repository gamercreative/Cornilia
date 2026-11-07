# Changelog

All notable changes to Cornilia. Versions follow [semantic versioning](https://semver.org).

## [Unreleased]

### Added
- Settings with defaults in `settings.py`, read from `CORNILIA_*` environment variables, with an optional local `configs.py` on top and a committed `configs.example.py` template. A fresh clone runs without a shared file. (#1)
- `requirements.txt` and `requirements-server.txt`. (#2)
- README. (#3)
- Provider presets (`ollama`, `openai`) and model selection through configuration. `model_names` is now an optional allow list. (#4)
- System prompt from configuration or a file, with `$date`, `$time`, `$datetime` and `$weekday` filled in when a conversation starts. (#5)
- `@tool` decorator and `FunctionTool`, which build a tool and its schema from a plain function. (#6)
- `LoadTools` loads tools from a folder (`CORNILIA_TOOLS_DIR`), skipping broken modules and duplicate names with a warning. (#7)
- Opt-in tool call tracing with `CORNILIA_TRACE=1`, or any function passed as `tool_tracer`. (#8)
- Working HTTP server: `POST /one-shot`, `POST /multi-shot`, `GET /multi-shot/{conversation_hash}`. (#13)
- `MultiShotMessage` as the correctly spelled name. `MutliShotMessage` still works.

### Fixed
- A failing model call is retried a bounded number of times without adding the user message again. When retries run out, a `RetriesExhaustedError` is raised and the history goes back to how it was before the message. (#9)
- The agent loop is a loop with a step limit (`CORNILIA_MAX_STEPS`) instead of unbounded recursion. (#10)
- Tool calls are detected from `message.tool_calls`. Every finish reason is handled, and an empty reply returns `""` instead of `"None"`. (#11)
- A reply cut off by the token limit keeps its partial text, so the continuation follows on from it and the whole reply is returned.
- System messages print `System_Error` and similar on Python 3.12 and newer, instead of `SystemResponseTypes.SystemError`.
- `main.py` no longer starts the chat loop when imported.
- Resetting a conversation renders the system prompt again, so `$date` and `$time` are current instead of the time the model was created.
- A `SYSTEM_PROMPT_FILE` that doesn't exist raises a clear error. `main.py` and `server.py` stop at startup with it, instead of failing on every message.
- `main.py` prints one error line for setup problems (no model, a missing prompt file, no API key) instead of a traceback.
- The server returns 502 for an unusable provider reply (`ProviderResponseError`), and drops a newly created conversation whatever error its first turn fails with.
- `requirements.txt` asks for `openai>=1.58`, which `reasoning_effort` needs. `requirements-server.txt` lists `pydantic>=2`.

### Changed
- `OpenAIModel` no longer keeps its own unused `MessageHistory`. `ResetModelMessageQueue` now clears the context manager. (#12)
- `ProviderConfigs` moved from `configs.py` to `providers.py`.
- The `TaxCalculator` example tool moved from `tools.py` to `examples/tools/`, and the `tools` dict in `tools.py` is replaced by `LoadTools` and `BuildToolRegistry`.
- An empty tools list is no longer sent to the provider.
- No tools are loaded by default. Set `CORNILIA_TOOLS_DIR`, for example to `examples/tools`.
- The SDK's own retries are off (`max_retries=0`). Cornilia retries once per provider call (`CORNILIA_MAX_RETRIES`).

### Migrating from an older `configs.py`
- Rename `OLLAMA_PROVIDER_CONFIGS` to `PROVIDER_CONFIGS`. Until you do, Cornilia prints a warning and uses the defaults.
- Set `MODEL_NAME`. The model is no longer chosen in `main.py`.
- Import `ProviderConfigs` with `from providers import ProviderConfigs`, since `configs.py` no longer defines it.
- Settings you leave out keep their default from `settings.py`. See `configs.example.py`.
