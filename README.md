# Cornilia

Cornilia is a small, general-purpose, provider-agnostic Python agent framework. It runs the agent loop (send the conversation to a model, run the tools the model asks for, feed the results back), keeps the conversation history, and lets any project plug in its own tools.

It talks to models through the OpenAI-compatible chat completions API, so the same code works with a local server such as [Ollama](https://ollama.com) or a hosted API.

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/gamercreative/Cornilia.git
cd Cornilia
pip install -r requirements.txt          # the agent loop only
pip install -r requirements-server.txt   # also the HTTP server
```

Releases are tagged. A project built on Cornilia should pin one instead of following `main`, for example `git clone --branch v0.1.0 https://github.com/gamercreative/Cornilia.git`. See [`CHANGELOG.md`](CHANGELOG.md) for what changed in each version.

## Configure

Every setting has a default in [`settings.py`](settings.py). Change them with environment variables, or copy [`configs.example.py`](configs.example.py) to `configs.py` (not committed) and set what you need there. Values in `configs.py` win over environment variables.

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
| `CORNILIA_TRACE` | off | Set to `1` to print every tool call, its arguments, result and time |

Local model with Ollama:

```bash
ollama pull qwen3.5:9b
python main.py
```

With the example tools and a trace of every tool call:

```bash
CORNILIA_TOOLS_DIR=examples/tools CORNILIA_TRACE=1 python main.py
```

Hosted OpenAI-compatible API:

```bash
CORNILIA_PROVIDER=openai OPENAI_API_KEY=sk-... CORNILIA_MODEL=<model name> python main.py
```

For any other hosted server, set `CORNILIA_BASE_URL` and `CORNILIA_API_KEY` and also `CORNILIA_PROVIDER=openai`. The default `ollama` preset sends `max_tokens` and `reasoning_effort`, which some hosted servers reject. OpenAI reasoning models take `max_completion_tokens` instead of `max_tokens`. Set it in `MODEL_RESPONSE_CONFIG` in `configs.py`.

The system prompt can use `$date`, `$time`, `$datetime` and `$weekday`. They are filled in when a conversation starts or is reset, for example `You are a helpful assistant. Today is $weekday $date.` Write `$$` for a literal `$`. A `CORNILIA_SYSTEM_PROMPT_FILE` that doesn't exist stops the program at startup with an error.

## Run

```bash
python main.py
```

That starts a chat loop in the terminal. Each message runs one turn of the agent loop. If a turn fails, the error is printed and the history goes back to how it was before that message.

### HTTP server

```bash
uvicorn server:app --reload                       # from the Cornilia folder
uvicorn --app-dir path/to/Cornilia server:app     # from anywhere else
```

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
from tools import tool

@tool
def count_words(text: Annotated[str, "The text to count the words of"]) -> dict:
    """Counts the words and characters in a piece of text."""
    return {"words": len(text.split()), "characters": len(text)}
```

Parameters without a default are required. `Literal[...]` becomes an enum, and `list[str]`, `int | None` and similar hints work as you would expect.

For full control, subclass `Tool` and write `ToolFunction` and `get_schema` yourself. See [`examples/tools/tax_calculator.py`](examples/tools/tax_calculator.py).

Drop the file into your tools folder (`CORNILIA_TOOLS_DIR`) and it is loaded at startup. A file that fails to import, or a tool name already taken, is skipped with a printed warning.

## Contributing

Read [AGENTS.md](AGENTS.md) first. The main rule is to keep the core general: project- or event-specific tools, prompts and setups live outside it and configure Cornilia instead of changing it.
