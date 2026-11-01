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
