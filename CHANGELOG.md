# Changelog

Downstream projects pin a tagged release, so every change in behaviour gets a line here.

## Unreleased

- Added `examples/benchmark`: a model benchmark that runs through Cornilia's own agent loop with deterministic fake
  tools and deterministic scoring (tool call validity, arguments, answers, hidden asserts for code). Quick and full
  suites, optional thinking comparison, and one results folder per run named after the date, model and Cornilia
  version. Includes the 2026-11-13 reference run of qwen3.5:35b-a3b. No change to the core.
- Added a planner/executor team (`cornilia.orchestration.PlannerExecutor`): a planner model thinks and gives
  instructions, an executor model carries them out with the tools. Turn it on in the terminal chat with
  `CORNILIA_PLANNER_MODEL`, and change or swap the two models while it runs with `/planner`, `/executor` and `/swap`.
- Added `CreatePlannerExecutor` and a `response_configs` argument to `CreateModel` in `cornilia.main`.
- Added `MaxRoundsError` and the `TeamPrompts` system prompts.
- Tool parameters can be nested objects: a `TypedDict` hint becomes a JSON schema object with its fields, also inside lists.
- Tool arguments are converted to the hinted type when the model sends them as text (`"10"`, `"true"`, a JSON string for a
  list or object), and a `null` for an argument with a default uses the default. This mostly helps smaller local models.
- Tool calls without an id get one, and arguments sent as an object instead of a JSON string are accepted.
- A tool call cut off by the token limit now raises `MaxTokenError` with a hint, instead of running with broken arguments.
- Added `CORNILIA_TIMEOUT` for the provider request timeout.
- Added `github_triage_issues` to the GitHub example tools: one call checks many issues against a list of nested rules
  and plans (or, when allowed, applies) labels, comments and closing.
- Added a test suite (`pip install -e ".[dev]"`, then `pytest`).

## 0.1.0

- First tagged release: the agent loop, conversation memory, pluggable tools, the terminal chat and the HTTP server.
