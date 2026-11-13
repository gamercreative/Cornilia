# Benchmark results

One row per run folder in [`results/`](results/). Each folder has the full `REPORT.md`, `run.json` and, for `bench.py`
runs, `tasks.jsonl`. `bench.py` adds a row here when a run in the default folder finishes.

The first row is the reference run (qwen3.5:35b-a3b via Ollama on a 32 GB MacBook Pro, 2026-11-13). It was made with an
earlier script that is not in the repository, so its numbers are not directly comparable with `bench.py` runs: there
the JSON column counts 6 schemas twice, coding and reasoning show thinking off / on, and tool tasks include 8 nested
tasks against the live GitHub API.

Tool tasks are `simple`, `chain`, `nested` and `filters` together. Long context is the needle tasks. tok/s is the
median over the run.

| Date | Model | Cornilia | Suite | Tool tasks | JSON | Coding | Reasoning | Long context | tok/s | Run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-13 | qwen3.5:35b-a3b | 0.1.0 (32483e3) | earlier script | 58/61 | 12/12 | 6/6 off, 6/6 on | 6/8 off, 8/8 on | 12/12 | ~40 | [2026-11-13_qwen3.5-35b-a3b-q4_K_M_cornilia-0.1.0-32483e3](results/2026-11-13_qwen3.5-35b-a3b-q4_K_M_cornilia-0.1.0-32483e3/REPORT.md) |
