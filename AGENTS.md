# AGENTS.md

Instructions for AI coding agents and contributors working on Cornilia.

> **This file is maintained by the repository owner. Do not edit, move, rename or delete it,
> and do not edit `CLAUDE.md` or `.github/CODEOWNERS`.** If something here looks wrong or out
> of date, say so in an issue or in your reply instead of changing it.

## What Cornilia is

Cornilia is a **general-purpose, provider-agnostic Python agent framework**. It runs the agent
loop (send the conversation to a model, run the tools the model asks for, feed the results
back), manages the conversation context, and lets any project plug in its own tools.

It is meant to be reused across many projects, so it must stay general.

## The core rule: keep the core general

- **No domain- or event-specific code in the core.** No tools, prompts, data or behaviour
  written for one project, course, workshop, hackathon or client.
- **Specialize from the outside.** Project and event setups (for example a workshop starter)
  live in their own repository, or under `examples/`, and depend on a **tagged Cornilia
  release**. They configure Cornilia; they don't change it.
- **Extend through general mechanisms.** If a use case needs something the core can't do,
  add a general capability (a config option, a hook, a new interface) that any project could
  use, never a special case for that one use.
- **Stay provider-agnostic.** Talk to models through the OpenAI-compatible chat completions
  interface. Provider details (base URL, key, model name) come from configuration, never from
  hardcoded values in the code.

When a request conflicts with these rules, don't implement the special case. Propose the
general version, or suggest putting it in a separate project or `examples/`.

## How the code is organized

| File | Responsibility |
| --- | --- |
| `main.py` | Entry point: wires a provider, a context manager, a model and tools, then runs the chat loop |
| `providers.py` | Connection to a model provider through the OpenAI-compatible API |
| `models.py` | The agent loop: sends messages, runs tool calls, handles finish reasons |
| `context_managers.py` | Conversation history (memory) |
| `tools.py` | The `Tool` interface and the tool registry |
| `system.py` | Shared enums and system prompts |
| `errors.py` | Error types |
| `server.py` | HTTP API (work in progress) |
| `configs.py` | Local configuration. **Not committed**, since it holds secrets |

## Design for small models

Cornilia has to work well with small local models, not only with large hosted ones. The
weakest model you want to support sets the bar. A typical one is Qwen3.5 35B-A3B through
Ollama, with about 3B active parameters. Testing in November 2026 showed what such models
handle well and where they slip. They call tools reliably and fill in fixed schemas well.
They slip on comparisons, on filters they have to infer, and on long free-form reasoning.
Build tools, prompts and the loop around that:

- **Code does the exact work, the model decides.** Parsing, cleaning, filtering, sorting,
  arithmetic, deduplication and validation happen in code inside tools. The model picks
  which tool to call and fills in the arguments.
- **Keep tool results small.** Return a short summary. Aim for about 2,000 characters or
  less. Large data goes to a file, and the result says where it is. Long text comes back in
  parts, with a way to ask for the next part.
- **Make tool interfaces hard to get wrong.** Use few parameters with sensible defaults,
  `Literal` choices instead of free text where the options are known, and short
  descriptions. Make filters explicit (for example `state: "open" | "closed" | "all"`)
  instead of leaving the model to infer them.
- **Errors teach.** A failed tool call says what was wrong and what to try next, because
  the error text goes back to the model.
- **Be lenient with what models send, strict with what gets saved.** Accept arguments in
  slightly wrong shapes where the meaning is clear (the core already converts types).
  Validate anything that gets stored or acted on.
- **Work with thinking off.** Thinking or reasoning modes may be an option, never a
  requirement. Slow answers must fit within configurable timeouts.
- **Keep the conversation append-only.** Don't rewrite or reorder earlier messages. Local
  servers reuse their prompt cache only when the start of the prompt stays the same.
- **No model-specific code in the core.** These are general rules. Never branch on a
  model or provider name.

## Benchmarking

`examples/benchmark/` measures how a model does through Cornilia: tool calls, multi-step
chains, nested arguments, filters and comparisons, JSON output and long context. It runs
against whatever model is configured.

Benchmarking is development work. Run it only when a change can affect how models behave,
and only at the scale the change needs:

| Change | What to run |
| --- | --- |
| Docs, comments, tests only | Nothing |
| A new or changed tool under `examples/` | That tool's tasks, or the `quick` suite |
| Tool schemas or argument handling, the loop in `models.py`, system prompts, orchestration | The `quick` suite on one small local model |
| A new release tag | The `full` suite, with repeats |

- **Never during a live event, workshop or demo.** It loads the machine and gets in the
  way of the work. Skip it, and run it later if it's still needed.
- **Report what you ran.** Put the model, the hardware and the pass rates in the pull
  request, and compare them with the latest run in `examples/benchmark/RESULTS.md`.
- **Never make up numbers.** If no model is available, say so in the pull request.

## Working rules

- **Keep it reproducible.** Every change must keep "fresh clone, install, configure, run"
  working. New dependencies go in the dependency manifest; new settings get a default or an
  entry in the config template.
- **Never commit secrets.** API keys and local settings come from environment variables or
  the uncommitted `configs.py`.
- **Small, focused changes.** One concern per change. Don't rename public classes or methods
  as a side effect of other work.
- **Prefer clear code over clever code.** Cornilia is also used to teach how agents work, so
  the loop should stay easy to read.
- **Releases are tagged.** Downstream projects pin a version, so behaviour changes go in a
  new release and get a line in the changelog.
