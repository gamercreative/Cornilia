# Cornilia

Cornilia is a small, general-purpose, provider-agnostic Python agent framework. It runs the agent loop (send the conversation to a model, run the tools the model asks for, feed the results back), keeps the conversation history, and lets any project plug in its own tools.

It talks to models through the OpenAI-compatible chat completions API, so the same code works with a local server such as [Ollama](https://ollama.com) or a hosted API.

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/gamercreative/Cornilia.git
cd Cornilia
pip install -e .            # the agent loop
pip install -e ".[server]"  # also the HTTP server
```

To use Cornilia in another project, install a tagged release instead of following `main`:

```bash
pip install "cornilia @ git+https://github.com/gamercreative/Cornilia.git@v0.1.0"
```

What changed in each version is listed on the [releases page](https://github.com/gamercreative/Cornilia/releases).

## Configure

Every setting has a default in [`settings.py`](src/cornilia/settings.py). Change them with environment variables, or copy [`configs.example.py`](configs.example.py) to `configs.py` in the folder you run from (it is gitignored) and set what you need there. Values in `configs.py` win over environment variables.

| Environment variable | Default | What it does |
| --- | --- | --- |
| `CORNILIA_PROVIDER` | `ollama` | Preset to start from: `ollama` (local) or `openai` (hosted) |
| `CORNILIA_MODEL` | `qwen3.5:9b` for `ollama`, none for `openai` | Model name |
| `CORNILIA_BASE_URL` | from the preset | Any other OpenAI-compatible server |
| `CORNILIA_API_KEY` | from the preset (`OPENAI_API_KEY` for `openai`) | API key |
| `CORNILIA_MAX_TOKENS` | `512` for `ollama` | Max tokens per reply. A longer reply is continued automatically |
| `CORNILIA_SYSTEM_PROMPT` / `CORNILIA_SYSTEM_PROMPT_FILE` | none | System prompt for every new conversation |
| `CORNILIA_TOOLS_DIR` | none | Folder of tool modules to load. Try `examples/tools` |
| `CORNILIA_MAX_STEPS` | `10` | Model responses allowed per turn, counting tool rounds |
| `CORNILIA_MAX_RETRIES` | `1` | Extra attempts when the provider has a connection, rate limit or server error |
| `CORNILIA_RETRY_MAX_WAIT` | `30` | Longest wait in seconds before a retry. Cornilia waits as long as the provider's `Retry-After` header asks, otherwise 2s, 4s, 8s and so on |
| `CORNILIA_TRACE` | off | Set to `1` to print every tool call, its arguments, result and time |

Local model with Ollama:

```bash
ollama pull qwen3.5:9b
python -m cornilia
```

With the example tools and a trace of every tool call:

```bash
CORNILIA_TOOLS_DIR=examples/tools CORNILIA_TRACE=1 python -m cornilia
```

On Windows, the `NAME=value command` form used in these examples doesn't work in Command Prompt or PowerShell. Put the same values in your `configs.py` instead, then run `python -m cornilia`:

| Environment variable | In `configs.py` |
| --- | --- |
| `CORNILIA_PROVIDER`, `CORNILIA_BASE_URL`, `CORNILIA_API_KEY` | `PROVIDER_CONFIGS = ProviderConfigs(...)`, see `configs.example.py` |
| `CORNILIA_MODEL` | `MODEL_NAME` |
| `CORNILIA_MAX_TOKENS` | `MODEL_RESPONSE_CONFIG = {"max_tokens": ...}` |
| `CORNILIA_TRACE=1` | `TRACE_TOOL_CALLS = True` |
| any other `CORNILIA_<NAME>` | `<NAME>`, for example `TOOLS_DIR = "examples/tools"` |

Hosted OpenAI-compatible API:

```bash
CORNILIA_PROVIDER=openai OPENAI_API_KEY=sk-... CORNILIA_MODEL=<model name> python -m cornilia
```

Google Gemini works the same way through its OpenAI-compatible endpoint:

```bash
CORNILIA_PROVIDER=openai CORNILIA_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/ \
  CORNILIA_API_KEY=$GEMINI_API_KEY CORNILIA_MODEL=<gemini model name> python -m cornilia
```

`configs.example.py` has the same setup as a commented `configs.py` block.

For any other hosted server, set `CORNILIA_BASE_URL` and `CORNILIA_API_KEY` and also `CORNILIA_PROVIDER=openai`. The default `ollama` preset sends `max_tokens` and `reasoning_effort`, which some hosted servers reject. OpenAI reasoning models take `max_completion_tokens` instead of `max_tokens`. Set it in `MODEL_RESPONSE_CONFIG` in `configs.py`.

The system prompt can use `$date`, `$time`, `$datetime` and `$weekday`. They are filled in when a conversation starts or is reset, for example `You are a helpful assistant. Today is $weekday $date.` Write `$$` for a literal `$`. A `CORNILIA_SYSTEM_PROMPT_FILE` that doesn't exist stops the program at startup with an error.

## Run

```bash
python -m cornilia    # or just: cornilia
```

That starts a chat loop in the terminal. Each message runs one turn of the agent loop. If a turn fails, the error is printed and the history goes back to how it was before that message.

### HTTP server

```bash
uvicorn cornilia.server:app --reload
```

Run it from the folder that holds your `configs.py` and tools, since relative paths are taken from there.

Interactive docs are at `http://localhost:8000/docs`.

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /one-shot` | `{"message", "model_name"?, "system_prompt"?}` | `{"model_name", "status", "response"}`, nothing remembered |
| `POST /multi-shot` | `{"message", "conversation_hash"?, "model_name"?}` | `{"conversation_hash", "model_name", "status", "response", "message_count"}`. Leave out `conversation_hash` to start a conversation |
| `GET /multi-shot/{conversation_hash}` | none | `{"conversation_hash", "model_name", "messages"}` |

`status` is `ok`, `content_filter` (the provider blocked the reply) or `max_steps` (the step limit was hit). In the last two cases `response` is `null`. Errors use FastAPI's `{"detail": "..."}` with 400 (no model set, or a model outside the provider's `model_names` allow list), 404 (unknown conversation), 422 (bad body) or 502 (the provider failed or sent an unusable reply). With no allow list, a model the provider doesn't serve comes back as 502. Conversations are kept in memory, so a restart forgets them.

## Write a tool

A tool is any `Tool` object defined at the top of a `.py` file in the tools folder. The shortest way is a plain function with the `@tool` decorator. The name, docstring and type hints become the schema the model sees:

```python
from typing import Annotated
from cornilia.tools import tool

@tool
def count_words(text: Annotated[str, "The text to count the words of"]) -> dict:
    """Counts the words and characters in a piece of text."""
    return {"words": len(text.split()), "characters": len(text)}
```

Parameters without a default are required. `Literal[...]` becomes an enum, and `list[str]`, `int | None` and similar hints work as you would expect.

For full control, subclass `Tool` and write `ToolFunction` and `get_schema` yourself. See [`examples/tools/tax_calculator.py`](examples/tools/tax_calculator.py).

Drop the file into your tools folder (`CORNILIA_TOOLS_DIR`) and it is loaded at startup. A tool file can import other modules from the folder you run from, such as your project's data. A file that fails to import, a file that defines no tools (for example a function missing its `@tool`), and a tool name already taken are each skipped with a printed warning. Files whose name starts with `_` are not loaded, so use that for helper modules.

## Use it from your own code

```python
from cornilia import main

provider = main.CreateProvider()
model = main.CreateModel(provider, main.LoadTools("tools"))
print(model.MultiShotMessage("What can you do?"))
```

`CreateModel` reads the same settings as the terminal chat. `MultiShotMessage` remembers the conversation. It raises `RetriesExhaustedError`, `MaxStepsError`, `ContentFilterError` or `ProviderResponseError` (all from `cornilia.errors`) when a turn fails, and leaves the history as it was before the message.

## Project layout

| Path | What it holds |
| --- | --- |
| `src/cornilia/main.py` | Wiring: `CreateProvider`, `CreateModel`, `CreateTools`, and the terminal chat |
| `src/cornilia/models.py` | The agent loop: sends messages, runs tool calls, handles finish reasons |
| `src/cornilia/providers.py` | The connection to an OpenAI-compatible provider |
| `src/cornilia/context_managers.py` | Conversation history |
| `src/cornilia/tools.py` | The `Tool` interface, `@tool`, and `LoadTools` |
| `src/cornilia/settings.py` | Every setting and its default |
| `src/cornilia/system.py`, `errors.py` | Shared enums, prompts and error types |
| `src/cornilia/server.py` | The HTTP API |
| `examples/tools/` | Example tools |

## Contributing

Read [AGENTS.md](AGENTS.md) first. The main rule is to keep the core general: project- or event-specific tools, prompts and setups live outside it and configure Cornilia instead of changing it.
