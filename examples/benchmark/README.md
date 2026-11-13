# Model benchmark

`bench.py` checks how well a model works through Cornilia's own agent loop. It is written for small local models
(for example a Qwen model on Ollama on a laptop), and works with any OpenAI-compatible provider Cornilia can use.

It is an example, not part of the core: it only uses Cornilia's public pieces (`CreateModel`, `OpenAIModel`, `@tool`)
and adds no dependencies.

## What it measures

Every task runs in a fresh conversation through `OpenAIModel.MultiShotMessage`, the same path the terminal chat uses.
The tools are fake and deterministic: a small in-memory shop with customers, orders, products and support issues. They
make no network calls and change nothing.

| Category | Tasks | What it checks |
| --- | --- | --- |
| `simple` | 9 | One tool call with the right arguments. Includes a list argument, an enum, two calls in one turn and a question that needs no tool |
| `chain` | 5 | 2 to 4 calls where one result feeds the next, one of them starting from a misspelled name |
| `nested` | 4 | A `TypedDict` argument or a list of objects |
| `filters` | 6 | Filters and comparisons: "open and closed", "open or pending", the highest or cheapest of three, "over 100", "not paid" |
| `json` | 4 | JSON output, two with `response_format` and two asked for in the prompt only |
| `coding` | 6 | A small Python function, checked against asserts the model never sees |
| `reasoning` | 6 | Short puzzles with one exact answer, no tools |
| `long_context` | 12 | A fact planted at 10%, 50% and 90% of a log of about 2k, 7k, 14k and 24k tokens, with two look-alike distractors |
| `speed` | 3 | Replies of about 8, 380 and 1200 tokens, for time to first token and tokens per second |

Scoring is deterministic. No model grades another.

- **Valid tool calls.** The raw arguments parse as a JSON object, the tool exists, the required arguments are there,
  there are no unknown arguments and every type matches exactly. `"10"` for an integer fails here even though
  Cornilia converts it before the tool runs, so the count shows what the model really sent.
- **Correct arguments.** The calls the task needs were made with the right values (case and list order are ignored).
- **Final answer.** Expected text or numbers, exact JSON values, or the hidden asserts for code. Tasks that ask for an
  `ANSWER:` line are checked on that line.
- **Leaks.** A tool call written into the reply text instead of being sent as a call is counted.

A task passes when there was no error, the answer matches and the arguments are correct. Tool tasks that run more than
once also give a stability figure: how many passed every time.

Timing is measured per task: total latency, time to first token and tokens per second (from the provider's usage
report), and prefill speed on the long-context tasks. Requests are streamed for the time to first token; `--no-stream`
uses Cornilia's provider unchanged and gives latency only.

## How to run it

Install Cornilia (`pip install -e .`), set up the provider and model the usual way (environment variables or
`configs.py`, see the main README), then from the repository root:

```bash
python examples/benchmark/bench.py                            # quick suite
python examples/benchmark/bench.py --suite full               # every task, 3 repeats
python examples/benchmark/bench.py --only filters,json        # some categories
python examples/benchmark/bench.py --suite full --only reasoning,coding --thinking compare
python examples/benchmark/bench.py --list                     # print the plan and stop
```

| Flag | Default | What it does |
| --- | --- | --- |
| `--suite quick\|full` | `quick` | `quick`: 15 tasks once. `full`: all 55 tasks |
| `--only A,B` | all | Only these categories |
| `--repeats N` | 1 (quick), 3 (full) | Runs per task. Long-context tasks always run once, they are slow |
| `--model NAME` | `CORNILIA_MODEL` | The model to test |
| `--timeout S` | `CORNILIA_TIMEOUT` | Seconds per request. Use 600 with thinking on |
| `--thinking [on\|compare]` | off | `on` for every task, `compare` runs coding and reasoning a second time with thinking on |
| `--out DIR` | `examples/benchmark/results` | Folder that gets one subfolder per run |
| `--no-stream` | | Plain requests, for a server that can't stream |

Thinking off keeps the provider's setting (the Ollama preset sends `reasoning_effort: none`) and raises `max_tokens` to
at least 2048. Thinking on sends `reasoning_effort: medium` and `max_tokens` 8192. A provider that doesn't take
`reasoning_effort` will fail those tasks with its error, which the report shows.

For the long-context tasks the server needs a context window of at least 32k tokens. Ollama's default is smaller and
it cuts long prompts without saying so, so start it with `OLLAMA_CONTEXT_LENGTH=32768 ollama serve` (or set `num_ctx`
in a Modelfile). The benchmark compares the prompt tokens the server reports with what it sent and flags a cut prompt.

### How long it takes

Rough figures for a 35B-A3B model on a 32 GB M1 Pro at about 40 tokens/s, worked out from the reference run's speeds,
not measured with this script yet:

| Run | Runs | Time |
| --- | --- | --- |
| `quick` | 15 | about 2 to 3 minutes |
| `full` | 141 | about 20 to 30 minutes, a quarter of it the long-context prefill |
| `full --thinking compare` | 177 | add about 20 to 30 minutes for the thinking reruns |
| `--only X` | | in proportion to the tasks picked |

A warm-up request loads the model first so the first task's time doesn't include loading.

## What it writes

Each run gets a folder `results/<date>_<model-tag>_cornilia-<version>-<commit>/`, for example
`2026-11-13_qwen3.5-35b-a3b-q4_K_M_cornilia-0.1.0-32483e3`. A second run with the same name gets `_2`.

- `REPORT.md`: setup, tests and scoring, results, every miss with the reply, and recommendations. There is a Notes
  section at the end for your own observations.
- `run.json`: date; Cornilia version, commit and branch; model name, tag, parameters, active parameters, quantization
  and Ollama digest; runtime name, version and API base (without credentials); hardware; context length; sampling;
  settings (max_tokens, thinking, max_steps, retries, timeout); and the totals per category. A value the script can't
  find is `null`, it never guesses. The Ollama version, digest, size, quantization, context length and Modelfile
  sampling come from Ollama's own API when the provider is Ollama. Active parameters are always `null`, no server
  reports them.
- `tasks.jsonl`: one line per task run with the reply, every tool call and its validity, and the timings.

A finished run in the default folder adds a row to [RESULTS.md](RESULTS.md). Interrupt a run with Ctrl+C and the runs so
far are still written, without the RESULTS.md row.

## When to run it

Run it only when a change can affect how the model behaves, and at the scale the change needs:

- a new model, a new quantization or a runtime upgrade: `--suite full`
- a change to the agent loop, the provider, tool schemas or argument handling: the quick suite, then `--only` the
  categories it touches with `--suite full` if something moved
- a change to one kind of behaviour, for example JSON output: `--only json`
- documentation, tests or code the model never sees: don't run it

Never run it during a live event or a demo. It keeps the model busy for minutes and loads the machine, which slows
everything else on it.

## Notes

- The coding tasks run the model's function. The code is checked first and only runs if it is nothing but definitions,
  imports only a few standard modules (`math`, `re`, `collections` and the like) and avoids `open`, `eval`, `exec`,
  dunder attributes and similar. It then runs in a separate isolated Python process in an empty temporary folder with
  a 10 second limit. That is a guard against accidents, not a sandbox.
- Sampling is left to the server, so repeats can differ. That is what the stability figure measures.
- Ollama keeps the start of a repeated prompt in its cache, so a repeated task's time to first token can be lower than
  the first run's.
- Not covered (they were in the reference run): concurrent requests, memory use, and tasks against a live GitHub API.
