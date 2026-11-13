# Benchmark: qwen3.5:35b-a3b on Cornilia 0.1.0

The reference run, done by hand on 2026-11-13 before `bench.py` existed.

> Made with an earlier benchmark script that is not in the repository, so there is no `tasks.jsonl` for this run.
> Its tasks, categories and scoring differ from `bench.py` in places (for example the nested tasks used a live GitHub
> API), so these numbers are not directly comparable with `bench.py` runs.

## Setup

| | |
| --- | --- |
| Date | 2026-11-13 |
| Cornilia | 0.1.0 at 32483e3, branch `claude/project-thread-or8a7s` (PR #26) |
| Model | `qwen3.5:35b-a3b`, a mixture of experts with about 36B parameters and about 3B active, Q4_K_M, 23 GB, 100% on the GPU |
| Runtime | Ollama 0.40.16 through its OpenAI-compatible API (`localhost:11434/v1`) |
| Hardware | MacBook Pro, M1 Pro, 32 GB |
| Context length | 32768 |
| Sampling | Modelfile defaults: temperature 1, top_k 20, top_p 0.95, presence_penalty 1.5 |
| Thinking off | `max_tokens` 2048 (4096 for the GitHub tasks), `reasoning_effort: none` |
| Thinking on | `max_tokens` 8192 |
| Agent loop | `OpenAIModel` with max_steps 12, no retries, timeout 600 s, a fresh conversation per task through `MultiShotMessage` |
| Run | Python 3.12, one request at a time after a warm-up, 123 tasks in 32 minutes |

## Tests and scoring

| Category | Tasks |
| --- | --- |
| Simple tools | 10, using 10 mock tools. Includes a list argument, an enum, two calls in parallel and one question that needs no tool |
| Multi-step chains | 8, with 2 to 7 dependent calls. One recovers from a misspelled name |
| Nested (GitHub) | 8, against the live GitHub API, read-only |
| JSON | 6 schemas, each with `response_format` and prompt-only |
| Coding | 6 tasks with hidden asserts run in a subprocess, thinking off and on |
| Reasoning | 8 puzzles with exact answers, thinking off and on |
| Long context | 12: a needle at 10%, 50% and 90% of about 1.8k, 7k, 14k and 24k tokens |
| Speed | Streamed replies of about 8, 380 and 1200 tokens, three times each, plus 2 concurrent requests |
| Stability | 7 tool tasks, 5 repeats each |

A tool call counted as valid only if the raw JSON parsed, the tool existed, the required arguments were present with
no unknown ones, and every type matched exactly (`"10"` for an integer failed).

## Results

- Tool calls: 144/144 valid, 0 leaked into the reply text. Correct arguments on 60/61 tasks.
- Simple: 10/10, median 3.3 s.
- Multi-step chains: 8/8, median 6.0 s, max 15.2 s, 3.8 calls per task.
- Nested (GitHub): 8/8, median 8.7 s.
- Stability: 32/35. Tool tasks overall: 58/61.
- JSON: 6/6 with `response_format` and 6/6 prompt-only, about 2.5 s each.
- Coding: 6/6 with thinking off (5.7 s) and 6/6 with it on (median 47.7 s, max 256 s).
- Reasoning: 6/8 with thinking off (5.9 s) and 8/8 with it on (median 34.7 s, max 110 s).
- Long context: 12/12, up to about 24k tokens.
- Speed: about 0.4 s to the first token and about 40 tokens/s (18 to 25 tokens/s inside the agent loop). Prefill about
  350 tokens/s, so a 24k prompt takes about 70 s before the first token. Two concurrent requests were served one after
  the other (5.3 s and 10.7 s).
- Memory: 31 GB used, 25 GB wired, swap grew from 3.0 to 8.7 GB.

## Misses

- **Three-customer comparison**, wrong 2 times out of 5. The tool calls were right; the comparison of the totals was wrong.
- **"Open or closed" filter**, the state was dropped once.
- **Two reasoning puzzles**, wrong with thinking off and right with thinking on.

## Recommendations

Drawn from the numbers above:

- Tool calling itself is solid (144/144 valid). The weak spot is comparing values the tools returned, and keeping every
  part of a filter. `bench.py` has a `filters` category for exactly these.
- Keep thinking off for tool work. Turn it on for puzzles and code only when a reply of 35 s to several minutes is fine.
- Long prompts are expensive here: 24k tokens take about 70 s of prefill. Keep the context small for interactive use.
- The server answers one request at a time, so parallel agents wait for each other on this machine.
- Memory is tight with this model on 32 GB (swap grew by almost 6 GB): close other heavy apps while it runs.

## Notes

The raw per-task results were not kept, and the Ollama model digest was not recorded.
