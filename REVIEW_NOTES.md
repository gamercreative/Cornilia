# Review notes: fixes for issues #1–#14 (uncommitted)

Delete this file before you commit.

## Where things are
- Branch: `fix/v0.1.0-issues` in `~/Documents/code/Cornilia` (moved from the hackathon2 submodule on 7 Nov), based on GitHub `main` `ab5e2d7`. Nothing is committed.
- Your local `main` (`a9fd9cd`) is untouched. GitHub force-pushed over it, so it is 1 commit ahead of GitHub `main` and 7 behind. Its server work was carried over to this branch by hand.
- Your private `configs.py` is untouched and gitignored. Its old-style names print a warning and are ignored (see "Still to do").
- Full review results (36 confirmed findings): `/private/tmp/claude-501/.../scratchpad/review.json`. Temporary, so it may be gone.

## Done and tested with a mocked provider
- **#1** `settings.py` reads defaults from `CORNILIA_*` environment variables, then your optional `configs.py` overrides them. `configs.example.py` is the template. A fresh clone without `configs.py` runs.
- **#2** `requirements.txt` and `requirements-server.txt`.
- **#3** `README.md`.
- **#4** Presets: `CORNILIA_PROVIDER=ollama|openai`, plus `CORNILIA_MODEL` and `CORNILIA_BASE_URL`. `model_names` is now an optional allow list.
- **#5** System prompt from a setting or a file, with `$date`, `$time`, `$datetime` and `$weekday` filled in (`RenderSystemPrompt` in `system.py`).
- **#6** `@tool` decorator and `FunctionTool` in `tools.py`.
- **#7** `LoadTools(folder)`. Broken files and duplicate names are skipped with a warning. `TaxCalculator` moved to `examples/tools/`.
- **#8** `CORNILIA_TRACE=1` prints each tool call (`PrintToolTrace`).
- **#9** Retries happen per provider call in `CallModel`, with no duplicate history. A failed turn rolls the history back. `MultiShotMessage` is the new name and the old `MutliShotMessage` still works.
- **#10** The loop has a step limit, `MAX_STEPS` (raises `MaxStepsError`).
- **#11** Tool calls are detected from `message.tool_calls`. Every finish reason is handled, and an empty reply returns `""`.
- **#12** The history lives only in the context manager.
- **#13** `server.py` works: `POST /one-shot`, `POST /multi-shot` and `GET /multi-shot/{hash}`, with `status` set to `ok`, `content_filter` or `max_steps`.
- **#14** `CHANGELOG.md` is drafted. Tagging needs commits, so it is your step.
- **Bugs not in any issue, also fixed:**
  - On Python 3.12 and newer, `f"{SystemResponseTypes.SystemError}"` printed the enum name. Fixed with `StrValueEnum` in `system.py`.
  - A reply cut off by the token limit lost its partial text.
  - `main.py` started the chat loop when imported.

## Review fixes already applied
- One retry layer: the SDK client now uses `max_retries=0`.
- The length-continuation prompt is sent as a user message.
- `ProviderResponseError` for unusable provider responses.
- `max_steps` and `max_retries` are clamped to sensible values.
- Non-JSON tool results are sent as text (`default=str`).
- Tools don't run on the last step.
- Bare `list` type hints get `items` in the schema.
- `@tool` functions can still be called directly.
- `tool(f, name=)` keeps the name.
- A tool whose schema fails to build is skipped.
- Tool modules are registered in `sys.modules`, so dataclasses work.
- Relative paths are looked up in the folder you run from first, then the Cornilia folder.
- `EnvInt` gives clear errors for bad numbers.
- `TOOLS_DIR` has no default now (keeps the core general).
- Old `configs.py` names print a warning.

## Review leftovers, applied on 4 Nov (still uncommitted)
- `requirements.txt` asks for `openai>=1.58`, and `requirements-server.txt` lists `pydantic>=2`.
- `server.py`: `ProviderResponseError` gets a 502, the cleanup of a new conversation catches any exception, and the system prompt is read once at startup (`system_prompt_template`).
- `main.py`: a missing prompt file raises `HarnessBaseError`, and setup errors at startup print one line and exit.
- `CacheModelContextManager` stores the raw template and renders it in `ResetConversation`. `CreateModel` passes the template.
- README, `configs.example.py` and the CHANGELOG (with a migration section) are updated.
- Mocked tests re-run, 13 of 13 pass. Startup errors checked: a missing prompt file, no API key, no model.

## Still to do
1. **Your `configs.py`** still uses the old names (`OLLAMA_PROVIDER_CONFIGS`, `MODEL_NAMES_AIR/PRO`, `BASE_URL`, `API_KEY`). They are ignored, apart from `MODEL_RESPONSE_CONFIG`. Port it to `PROVIDER_CONFIGS` and `MODEL_NAME` (see `configs.example.py`).
2. **Optional, not applied:** answer 409 right away for a busy conversation (`lock.acquire(blocking=False)` in `server.py`), instead of waiting.
3. **Real model test:** `CORNILIA_TOOLS_DIR=examples/tools CORNILIA_TRACE=1 python3.13 main.py` against Ollama, then a hosted API (this closes #15).
4. **Commit** one commit per issue group, delete this file, then tag `v0.1.0` and point the hackathon repo's submodule at the tag. It currently pins `a9fd9cd`, which isn't on GitHub.

## Suggestions, not done
- **8k context is tight for running tests.** Cut tool output to a fixed size (for example the last 2,000 characters). Raise Ollama's context (`OLLAMA_CONTEXT_LENGTH=16384 ollama serve`). Later, keep only the last N turns in the context manager (a new issue).
- Tool files can't import helper files from the same folder yet (a `sys.path` change; left out on purpose).
- Package the code as `cornilia/` with a `pyproject.toml`, so a tag can be `pip install`ed (#14).
- Commit as separate commits, one per issue group (setup #1/#2/#4; loop #9–#12; features #5–#8; server #13; docs #3/#14), as `AGENTS.md` asks.
- Your local `main` still has `a9fd9cd`. Once this branch is merged you can reset `main` to GitHub's: `git branch -f main origin/main` (run it while on another branch).

## Resume
In this session, say "continue from REVIEW_NOTES.md". In a new session, point Claude at this file.
