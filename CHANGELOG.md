# Changelog

Downstream projects pin a tagged release, so every change in behaviour gets a line here.

## Unreleased

- Added a planner/executor team (`cornilia.orchestration.PlannerExecutor`): a planner model thinks and gives
  instructions, an executor model carries them out with the tools. Turn it on in the terminal chat with
  `CORNILIA_PLANNER_MODEL`, and change or swap the two models while it runs with `/planner`, `/executor` and `/swap`.
- Added `CreatePlannerExecutor` and a `response_configs` argument to `CreateModel` in `cornilia.main`.
- Added `MaxRoundsError` and the `TeamPrompts` system prompts.
- Added a test suite (`pip install -e ".[dev]"`, then `pytest`).

## 0.1.0

- First tagged release: the agent loop, conversation memory, pluggable tools, the terminal chat and the HTTP server.
