"""
    A benchmark for the model Cornilia is configured with, run through Cornilia's own provider and agent loop.

        python examples/benchmark/bench.py                      # the quick suite, about 15 tasks
        python examples/benchmark/bench.py --suite full         # every task, 3 repeats, long context
        python examples/benchmark/bench.py --only filters,json  # some categories only
        python examples/benchmark/bench.py --list               # show the tasks and stop

    The provider, model and timeout come from Cornilia's settings (environment variables or configs.py), the flags
    override them. Each run gets its own folder under examples/benchmark/results with REPORT.md, run.json and
    tasks.jsonl. See README.md in this folder for what is measured and when to run it.
"""
import argparse
import dataclasses
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
try:
    import cornilia # noqa: F401
except ModuleNotFoundError: # running from a clone without `pip install -e .`
    sys.path.insert(0, str(ROOT / "src"))

import openai
from openai.types.chat import ChatCompletion

from cornilia.errors import HarnessBaseError
from cornilia.providers import OpenAIProvider

from bench_scoring import CheckToolCall, CleanAnswer, LeakedToolCall
from bench_tasks import ALL_TOOLS, CATEGORIES, SYSTEM_PROMPT, THINKING_CATEGORIES, SelectTasks, ToolRegistry

RESULTS_DIR = HERE / "results"
PLAIN_PROMPT = "You are a helpful assistant. Follow the user's instructions about the format of your reply exactly."
TOOL_CATEGORIES = ["simple", "chain", "nested", "filters"]
THINKING_EFFORT = "medium" # sent as reasoning_effort when thinking is on
MAX_TOKENS_OFF = 2048 # the least max_tokens a run gets with thinking off, and with it on
MAX_TOKENS_ON = 8192
FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}

# --- the provider: Cornilia's OpenAIProvider, timed, and streamed so the time to first token can be measured ---

@dataclasses.dataclass
class RequestRecord:
    latency: float
    ttft: float | None # seconds to the first streamed token, None when not streamed
    prompt_tokens: int | None
    completion_tokens: int | None
    tool_calls: list # (name, raw arguments) as the model sent them

    @property
    def tokens_per_second(self) -> float | None:
        generating = self.latency - (self.ttft or 0)
        if not self.completion_tokens or generating <= 0:
            return None
        return self.completion_tokens / generating

class MeasuredProvider(OpenAIProvider):
    def __init__(self, provider_configs, stream: bool = True):
        super().__init__(provider_configs)
        self.stream = stream
        self.records: list[RequestRecord] = []

    def OneShotMessage(self, prompt, model_name, tools, response_configs) -> ChatCompletion:
        started = time.perf_counter()
        ttft = None
        if self.stream:
            try:
                response, ttft = self.StreamedMessage(prompt, model_name, tools, response_configs, started)
            except openai.BadRequestError as e: # some servers reject stream_options, fall back to plain requests
                print(f"  warning: streamed request rejected ({e}), measuring without streaming from now on")
                self.stream = False
        if not self.stream:
            started = time.perf_counter()
            response = super().OneShotMessage(prompt, model_name, tools, response_configs)
        usage = getattr(response, "usage", None)
        message = response.choices[0].message if response.choices else None
        calls = [(call.function.name, call.function.arguments) for call in (getattr(message, "tool_calls", None) or [])]
        self.records.append(RequestRecord(time.perf_counter() - started, ttft, getattr(usage, "prompt_tokens", None),
                                          getattr(usage, "completion_tokens", None), calls))
        return response

    # the same request as OpenAIProvider.OneShotMessage, streamed, put back together into one ChatCompletion
    def StreamedMessage(self, prompt, model_name, tools, response_configs, started) -> tuple[ChatCompletion, float | None]:
        if not self.IsModelAvailable(model_name):
            raise HarnessBaseError(f"model {model_name} not available, use one of {self.available_models}")
        configs = {**response_configs, "tools": tools} if tools else dict(response_configs)
        chunks = self.client.chat.completions.create(model=model_name, messages=prompt, stream=True,
                                                     stream_options={"include_usage": True}, **configs)
        content, calls, open_calls = [], [], {}
        finish_reason, usage, ttft = None, None, None
        for chunk in chunks:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            for choice in chunk.choices or []:
                delta = choice.delta
                extra = getattr(delta, "model_extra", None) or {}
                reasoning = extra.get("reasoning") or extra.get("reasoning_content")
                if ttft is None and (delta.content or delta.tool_calls or reasoning):
                    ttft = time.perf_counter() - started
                if delta.content:
                    content.append(delta.content)
                for part in delta.tool_calls or []:
                    key = part.index if part.index is not None else len(calls)
                    current = open_calls.get(key)
                    # some servers number every call 0, a new id at the same index is a new call
                    if current is None or (part.id and current["id"] and part.id != current["id"]):
                        current = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        calls.append(current)
                        open_calls[key] = current
                    if part.id:
                        current["id"] = part.id
                    if part.function and part.function.name and part.function.name != current["function"]["name"]:
                        current["function"]["name"] += part.function.name
                    if part.function and part.function.arguments:
                        arguments = part.function.arguments
                        current["function"]["arguments"] += arguments if isinstance(arguments, str) else json.dumps(arguments)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        message = {"role": "assistant", "content": "".join(content) or None}
        if calls:
            message["tool_calls"] = calls
        response = ChatCompletion.model_validate({
            "id": "streamed", "object": "chat.completion", "created": int(time.time()), "model": model_name,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": finish_reason if finish_reason in FINISH_REASONS else "stop"}],
            "usage": usage.model_dump() if usage else None,
        })
        return response, ttft

# --- what the run used: Cornilia, the model, the runtime and the machine. a value that can't be found is None ---

def Command(*command: str) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, cwd=ROOT)
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout.strip() or None) if result.returncode == 0 else None

def CorniliaInfo() -> dict:
    version = None
    try:
        from importlib.metadata import version as package_version
        version = package_version("cornilia")
    except Exception:
        pyproject = ROOT / "pyproject.toml"
        if pyproject.is_file():
            match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
            version = match.group(1) if match else None
    return {"version": version, "commit": Command("git", "rev-parse", "--short", "HEAD"),
            "branch": Command("git", "rev-parse", "--abbrev-ref", "HEAD")}

# the base url without a user, password or query string
def SafeUrl(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))

def FetchJson(url: str, body: dict | None = None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None

def IsOllama(provider_name: str, base_url: str) -> bool:
    return provider_name.lower() == "ollama" or ":11434" in base_url

SAMPLING_KEYS = {"temperature", "top_k", "top_p", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty", "seed"}

def ParseNumber(text: str):
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text

# the Ollama version, model digest, size, quantization, context length and modelfile sampling, from its native api
def OllamaInfo(base_url: str, model_name: str) -> dict:
    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    info = {"version": None, "digest": None, "parameters": None, "quantization": None, "context_length": None, "sampling": {}}
    version = FetchJson(root + "/api/version")
    info["version"] = (version or {}).get("version")
    show = FetchJson(root + "/api/show", {"model": model_name}) or {}
    details = show.get("details") or {}
    info["parameters"] = details.get("parameter_size")
    info["quantization"] = details.get("quantization_level")
    for line in (show.get("parameters") or "").splitlines():
        key, _, value = line.strip().partition(" ")
        value = value.strip().strip('"')
        if key == "num_ctx":
            info["context_length"] = ParseNumber(value)
        elif key in SAMPLING_KEYS:
            info["sampling"][key] = ParseNumber(value)
    for listing, field in ((FetchJson(root + "/api/ps"), "models"), (FetchJson(root + "/api/tags"), "models")):
        for entry in (listing or {}).get(field) or []:
            if model_name in (entry.get("name"), entry.get("model")):
                info["digest"] = info["digest"] or entry.get("digest")
                if entry.get("context_length"): # the context the loaded model really has, newer Ollama reports it
                    info["context_length"] = entry["context_length"]
    return info

def HardwareInfo() -> dict:
    system = platform.system()
    info = {"system": f"{system} {platform.release()}".strip() or None, "machine": platform.machine() or None,
            "model": None, "cpu": None, "memory_gb": None}
    if system == "Darwin":
        info["model"] = Command("sysctl", "-n", "hw.model")
        info["cpu"] = Command("sysctl", "-n", "machdep.cpu.brand_string")
        memory = Command("sysctl", "-n", "hw.memsize")
        info["memory_gb"] = round(int(memory) / 2**30) if memory and memory.isdigit() else None
    elif system == "Linux":
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text()
            match = re.search(r"model name\s*:\s*(.+)", cpuinfo)
            info["cpu"] = match.group(1).strip() if match else None
            match = re.search(r"MemTotal:\s*(\d+) kB", Path("/proc/meminfo").read_text())
            info["memory_gb"] = round(int(match.group(1)) / 2**20) if match else None
        except OSError:
            pass
    else:
        info["cpu"] = platform.processor() or None
    return info

# the model's name for a folder: qwen3.5:35b-a3b with Q4_K_M becomes qwen3.5-35b-a3b-q4_K_M
def ModelTag(model_name: str, quantization: str | None) -> str:
    tag = model_name
    if quantization and quantization.lower() not in model_name.lower():
        tag += "-" + quantization[0].lower() + quantization[1:]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", tag).strip("-")

def RunFolder(parent: Path, date: str, model_tag: str, cornilia_info: dict) -> Path:
    name = f"{date}_{model_tag}_cornilia-{cornilia_info['version'] or 'unknown'}-{cornilia_info['commit'] or 'nocommit'}"
    folder = parent / name
    number = 2
    while folder.exists():
        folder = parent / f"{name}_{number}"
        number += 1
    return folder

# --- running the tasks ---

def ResponseConfigs(base: dict, thinking: bool) -> dict:
    configs = dict(base)
    if thinking:
        configs["reasoning_effort"] = THINKING_EFFORT
        configs["max_tokens"] = max(configs.get("max_tokens") or 0, MAX_TOKENS_ON)
    elif "max_tokens" in configs:
        configs["max_tokens"] = max(configs["max_tokens"] or 0, MAX_TOKENS_OFF)
    return configs

# every (task, repeat, thinking) to run, in order
def Plan(tasks: list, repeats: int, thinking: str) -> list[tuple]:
    plan = []
    for task in tasks:
        for repeat in range(1, (repeats if task.repeat else 1) + 1):
            plan.append((task, repeat, thinking == "on"))
    if thinking == "compare":
        for task in tasks:
            if task.category in THINKING_CATEGORIES:
                for repeat in range(1, (repeats if task.repeat else 1) + 1):
                    plan.append((task, repeat, True))
    return plan

def GroupName(category: str, thinking: bool) -> str:
    return f"{category}+thinking" if thinking else category

def Median(values):
    values = [value for value in values if value is not None]
    return round(statistics.median(values), 3) if values else None

def RunOne(main, provider: MeasuredProvider, model_name: str, base_configs: dict, task, repeat: int, thinking: bool) -> dict:
    tools = ToolRegistry() if task.tools else {}
    model = main.CreateModel(provider, tools, model_name=model_name, system_prompt=SYSTEM_PROMPT if task.tools else PLAIN_PROMPT,
                             response_configs={**ResponseConfigs(base_configs, thinking), **task.response_configs})
    model.tool_tracer = None # the provider records every call
    provider.records = []
    prompt = task.Prompt()
    started = time.perf_counter()
    answer, error = "", None
    try:
        answer = model.MultiShotMessage(prompt)
    except (HarnessBaseError, openai.OpenAIError) as e:
        error = f"{type(e).__name__}: {e}"
    latency = time.perf_counter() - started

    schemas = {name: item.get_schema() for name, item in tools.items()}
    calls = [CheckToolCall(name, raw, schemas) for record in provider.records for name, raw in record.tool_calls]
    args_ok = task.calls.matches(calls) if task.calls is not None else None
    answer_ok, note = task.check(answer) if error is None else (False, "")
    leaked = LeakedToolCall(answer, [item.name for item in ALL_TOOLS])
    passed = error is None and answer_ok and args_ok is not False

    first = provider.records[0] if provider.records else None
    completion = sum(record.completion_tokens or 0 for record in provider.records) or None
    generating = sum(record.latency - (record.ttft or 0) for record in provider.records)
    notes = [note] if note else []
    if args_ok is False:
        notes.append(f"expected {task.calls.describe()}")
    if error:
        notes.append(error[:300])
    if leaked:
        notes.append("a tool call was written into the reply text")
    if task.expected_prompt_tokens and first and first.prompt_tokens and first.prompt_tokens < 0.7 * task.expected_prompt_tokens:
        notes.append(f"the server saw {first.prompt_tokens} prompt tokens, about {task.expected_prompt_tokens} were sent: "
                     f"the context window is probably too small")

    return {
        "task": task.id, "category": task.category, "group": GroupName(task.category, thinking), "repeat": repeat,
        "thinking": thinking, "passed": bool(passed), "answer_ok": bool(answer_ok), "args_ok": args_ok,
        "calls_total": len(calls), "calls_valid": sum(call.valid for call in calls), "leaked_tool_call": leaked,
        "requests": len(provider.records), "latency_s": round(latency, 3),
        "ttft_s": round(first.ttft, 3) if first and first.ttft is not None else None,
        "prompt_tokens": first.prompt_tokens if first else None, "completion_tokens": completion,
        "tokens_per_s": round(completion / generating, 1) if completion and generating > 0 else None,
        "prefill_tokens_per_s": round(first.prompt_tokens / first.ttft, 1) if first and first.prompt_tokens and first.ttft else None,
        "expected": task.check.describe(), "notes": "; ".join(notes), "error": error,
        "answer": CleanAnswer(answer)[:1500], "tool_calls": [call.to_dict() for call in calls],
    }

# --- summaries and reports ---

def Summarize(runs: list[dict]) -> dict:
    groups = {}
    for run in runs:
        groups.setdefault(run["group"], []).append(run)
    order = [GroupName(category, thinking) for thinking in (False, True) for category in CATEGORIES]
    totals = {}
    for name in sorted(groups, key=lambda group: order.index(group) if group in order else len(order)):
        items = groups[name]
        with_args = [run for run in items if run["args_ok"] is not None]
        totals[name] = {
            "passed": sum(run["passed"] for run in items), "runs": len(items),
            "tasks": len({run["task"] for run in items}),
            "calls_valid": sum(run["calls_valid"] for run in items), "calls_total": sum(run["calls_total"] for run in items),
            "args_ok": sum(bool(run["args_ok"]) for run in with_args), "args_checked": len(with_args),
            "median_latency_s": Median(run["latency_s"] for run in items),
            "max_latency_s": max(run["latency_s"] for run in items),
            "median_ttft_s": Median(run["ttft_s"] for run in items),
            "median_tokens_per_s": Median(run["tokens_per_s"] for run in items),
        }

    tool_runs = [run for run in runs if run["category"] in TOOL_CATEGORIES]
    by_task = {}
    for run in tool_runs:
        by_task.setdefault((run["task"], run["thinking"]), []).append(run["passed"])
    repeated = {key: results for key, results in by_task.items() if len(results) > 1}
    long_runs = [run for run in runs if run["category"] == "long_context"]
    return {
        "categories": totals,
        "calls_valid": sum(run["calls_valid"] for run in runs), "calls_total": sum(run["calls_total"] for run in runs),
        "leaked_tool_calls": sum(run["leaked_tool_call"] for run in runs),
        "args_ok": sum(bool(run["args_ok"]) for run in runs if run["args_ok"] is not None),
        "args_checked": sum(run["args_ok"] is not None for run in runs),
        "tool_tasks": {"passed": sum(run["passed"] for run in tool_runs), "runs": len(tool_runs)},
        # stability: tool tasks run more than once, how many passed every time
        "stability": {"tasks_always_passed": sum(all(results) for results in repeated.values()), "tasks": len(repeated),
                      "passed": sum(sum(results) for results in repeated.values()),
                      "runs": sum(len(results) for results in repeated.values())} if repeated else None,
        "passed": sum(run["passed"] for run in runs), "runs": len(runs),
        "median_ttft_s": Median(run["ttft_s"] for run in runs),
        "median_tokens_per_s": Median(run["tokens_per_s"] for run in runs),
        "median_prefill_tokens_per_s": Median(run["prefill_tokens_per_s"] for run in long_runs),
    }

def Fraction(passed, total) -> str:
    return f"{passed}/{total}" if total else "-"

def Seconds(value) -> str:
    return f"{value:.1f}" if value is not None else "-"

TABLE_HEADER = ["category", "passed", "valid calls", "args ok", "median s", "max s", "ttft s", "tok/s"]

def TableRows(summary: dict) -> list[list[str]]:
    return [[name, Fraction(group["passed"], group["runs"]), Fraction(group["calls_valid"], group["calls_total"]),
             Fraction(group["args_ok"], group["args_checked"]), Seconds(group["median_latency_s"]),
             Seconds(group["max_latency_s"]), Seconds(group["median_ttft_s"]), Seconds(group["median_tokens_per_s"])]
            for name, group in summary["categories"].items()]

def ConsoleTable(summary: dict) -> str:
    rows = [TABLE_HEADER] + TableRows(summary)
    widths = [max(len(row[index]) for row in rows) for index in range(len(TABLE_HEADER))]
    lines = ["  ".join(cell.ljust(width) if index == 0 else cell.rjust(width) for index, (cell, width) in enumerate(zip(row, widths)))
             for row in rows]
    lines.insert(1, "-" * len(lines[0]))
    return "\n".join(lines)

def Headlines(summary: dict) -> list[str]:
    lines = [f"valid tool calls {Fraction(summary['calls_valid'], summary['calls_total'])}, "
             f"{summary['leaked_tool_calls']} leaked into the reply text",
             f"correct arguments {Fraction(summary['args_ok'], summary['args_checked'])} runs, "
             f"tool tasks {Fraction(summary['tool_tasks']['passed'], summary['tool_tasks']['runs'])}"]
    if summary["stability"]:
        stability = summary["stability"]
        lines.append(f"stability: {stability['tasks_always_passed']}/{stability['tasks']} repeated tool tasks passed every "
                     f"time ({stability['passed']}/{stability['runs']} runs)")
    lines.append(f"median time to first token {Seconds(summary['median_ttft_s'])} s, median {Seconds(summary['median_tokens_per_s'])} tok/s"
                 + (f", prefill about {summary['median_prefill_tokens_per_s']:.0f} tok/s" if summary["median_prefill_tokens_per_s"] else ""))
    return lines

def Recommendations(summary: dict, runs: list[dict]) -> list[str]:
    tips = []
    if summary["calls_valid"] < summary["calls_total"]:
        tips.append("Some tool calls did not fit their schema. Look at the misses: Cornilia converts text like \"10\" to a number "
                    "before running the tool, but a model that does this often may need simpler tool signatures.")
    if summary["leaked_tool_calls"]:
        tips.append("Tool calls showed up in the reply text: check the server's tool-calling template for this model.")
    if any("context window" in run["notes"] for run in runs):
        tips.append("The server cut long prompts. Raise its context length (for Ollama: OLLAMA_CONTEXT_LENGTH or num_ctx).")
    for name, group in summary["categories"].items():
        if group["runs"] and group["passed"] / group["runs"] < 0.8:
            tips.append(f"{name} passed {Fraction(group['passed'], group['runs'])}: see the misses before relying on it.")
    categories = summary["categories"]
    for category in THINKING_CATEGORIES:
        off, on = categories.get(category), categories.get(GroupName(category, True))
        if off and on and on["passed"] / on["runs"] > off["passed"] / off["runs"]:
            tips.append(f"{category} does better with thinking on ({Fraction(on['passed'], on['runs'])} against "
                        f"{Fraction(off['passed'], off['runs'])}), at a median {Seconds(on['median_latency_s'])} s against "
                        f"{Seconds(off['median_latency_s'])} s.")
    return tips or ["No category stands out. Compare with earlier runs in RESULTS.md."]

def MarkdownTable(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    return "\n".join(lines + ["| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |" for row in rows])

def Report(meta: dict, summary: dict, runs: list[dict]) -> str:
    model, runtime, settings_used = meta["model"], meta["runtime"], meta["settings"]
    setup = [["Date", meta["date"]], ["Cornilia", f"{meta['cornilia']['version']} at {meta['cornilia']['commit']} ({meta['cornilia']['branch']})"],
             ["Model", f"{model['name']} ({model['parameters'] or 'size unknown'}, {model['quantization'] or 'quantization unknown'})"],
             ["Runtime", f"{runtime['name']} {runtime['version'] or ''} at {runtime['api_base']}".replace("  ", " ")],
             ["Hardware", ", ".join(f"{key} {value}" for key, value in meta["hardware"].items() if value) or "unknown"],
             ["Context length", meta["context_length"] or "unknown"], ["Sampling", json.dumps(meta["sampling"]) if meta["sampling"] else "unknown"],
             ["Settings", ", ".join(f"{key} {value}" for key, value in settings_used.items() if value is not None)],
             ["Suite", f"{meta['suite']}, {meta['repeats']} repeat(s), {meta['streamed'] and 'streamed' or 'not streamed'}, "
                       f"{summary['runs']} runs in {meta['duration_s'] / 60:.1f} min" + (" (interrupted)" if meta["interrupted"] else "")]]
    misses = [run for run in runs if not run["passed"]]
    lines = [f"# Benchmark: {model['name']} on Cornilia {meta['cornilia']['version']}", "",
             "Written by `examples/benchmark/bench.py`. Add your own notes at the end.", "",
             "## Setup", "", MarkdownTable(["", ""], setup), "",
             "## Tests and scoring", "",
             "Every task runs in a fresh conversation through `OpenAIModel.MultiShotMessage`, with the benchmark's fake tools "
             "(in-memory customers, orders, products and issues). Scoring is deterministic, no model grades another:", "",
             "- a tool call is valid when its raw arguments parse as a JSON object, the tool exists, the required arguments "
             "are there, there are no unknown arguments, and every type matches exactly (`\"10\"` for an integer fails)",
             "- the arguments are correct when the calls the task needs were made with the right values",
             "- the final answer is checked by rule: expected text or numbers, exact JSON, or hidden asserts for code",
             "- a task passes when there was no error, the answer matches and the arguments are correct", "",
             "## Results", ""] + [f"- {line}" for line in Headlines(summary)] + ["", MarkdownTable(TABLE_HEADER, TableRows(summary)), "",
             f"## Misses ({len(misses)})", ""]
    if not misses:
        lines.append("None.")
    for run in misses:
        lines.append(f"- **{run['task']}**{' (thinking)' if run['thinking'] else ''}, repeat {run['repeat']}: "
                     f"{run['notes'] or 'answer did not match'}. Expected {run['expected']}. "
                     f"Reply: `{run['answer'][:200].replace(chr(10), ' ').replace('`', chr(39))}`")
    lines += ["", "## Recommendations", ""] + [f"- {tip}" for tip in Recommendations(summary, runs)] + ["", "## Notes", "", "", ""]
    return "\n".join(lines)

def ResultsRow(meta: dict, summary: dict, folder: Path) -> str:
    categories = summary["categories"]
    def Cell(*names):
        groups = [categories[name] for name in names if name in categories]
        return Fraction(sum(group["passed"] for group in groups), sum(group["runs"] for group in groups))
    return (f"| {meta['date']} | {meta['model']['name']} | {meta['cornilia']['version']} ({meta['cornilia']['commit']}) | "
            f"{meta['suite']} | {Fraction(summary['tool_tasks']['passed'], summary['tool_tasks']['runs'])} | {Cell('json')} | "
            f"{Cell('coding')} | {Cell('reasoning')} | {Cell('long_context')} | {Seconds(summary['median_tokens_per_s'])} | "
            f"[{folder.name}](results/{folder.name}/REPORT.md) |")

def WriteResults(folder: Path, meta: dict, summary: dict, runs: list[dict]):
    folder.mkdir(parents=True, exist_ok=True)
    run_json = {key: meta[key] for key in ("date", "cornilia", "model", "runtime", "hardware", "context_length", "sampling", "settings")}
    run_json.update({"suite": meta["suite"], "repeats": meta["repeats"], "streamed": meta["streamed"],
                     "duration_s": meta["duration_s"], "interrupted": meta["interrupted"], "totals": summary})
    (folder / "run.json").write_text(json.dumps(run_json, indent=2) + "\n", encoding="utf-8")
    with open(folder / "tasks.jsonl", "w", encoding="utf-8") as file:
        for run in runs:
            file.write(json.dumps(run) + "\n")
    (folder / "REPORT.md").write_text(Report(meta, summary, runs), encoding="utf-8")
    index = HERE / "RESULTS.md"
    if folder.parent.resolve() == RESULTS_DIR.resolve() and index.is_file() and not meta["interrupted"]:
        with open(index, "a", encoding="utf-8") as file:
            file.write(ResultsRow(meta, summary, folder) + "\n")

# --- command line ---

def ParseArguments(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark the configured model through Cornilia's agent loop.")
    parser.add_argument("--suite", choices=["quick", "full"], default="quick", help="quick: about 15 tasks once; full: every task")
    parser.add_argument("--only", help=f"comma-separated categories: {','.join(CATEGORIES)}")
    parser.add_argument("--repeats", type=int, help="runs per task, default 1 for quick and 3 for full (long context runs once)")
    parser.add_argument("--model", help="model name, default CORNILIA_MODEL / MODEL_NAME")
    parser.add_argument("--timeout", type=float, help="seconds per request, default CORNILIA_TIMEOUT")
    parser.add_argument("--thinking", nargs="?", const="on", default="off", choices=["off", "on", "compare"],
                        help="off (default), on, or compare: run coding and reasoning again with thinking on")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="folder that gets one subfolder per run")
    parser.add_argument("--no-stream", action="store_true", help="plain requests: no time to first token")
    parser.add_argument("--list", action="store_true", help="print the tasks the run would do and stop")
    return parser.parse_args(argv)

def main(argv=None):
    args = ParseArguments(argv)
    only = [name.strip() for name in args.only.split(",") if name.strip()] if args.only else None
    try:
        tasks = SelectTasks(args.suite, only)
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    repeats = args.repeats if args.repeats is not None else (3 if args.suite == "full" else 1)
    plan = Plan(tasks, max(repeats, 1), args.thinking)

    if args.list:
        for task, repeat, thinking in plan:
            print(f"{task.id}{' +thinking' if thinking else ''} #{repeat}")
        print(f"{len(tasks)} tasks, {len(plan)} runs")
        return

    from cornilia import main as cornilia_main # reads settings and configs.py
    from cornilia import settings

    configs = settings.PROVIDER_CONFIGS
    if args.timeout:
        configs = dataclasses.replace(configs, timeout=args.timeout)
    model_name = args.model or settings.MODEL_NAME
    if not model_name:
        raise SystemExit("error: no model set, use --model or CORNILIA_MODEL")
    if args.thinking != "off" and configs.timeout < 600:
        print(f"note: thinking replies can take minutes, the timeout is {configs.timeout:g}s (raise it with --timeout 600)")
    provider = MeasuredProvider(configs, stream=not args.no_stream)
    base_configs = dict(settings.MODEL_RESPONSE_CONFIG)

    print(f"benchmark: {model_name} on {configs.name} ({SafeUrl(configs.base_url)}), {args.suite} suite, "
          f"{len(tasks)} tasks, {len(plan)} runs, thinking {args.thinking}")
    warmup_started = time.perf_counter()
    try: # loads the model, so the first task's time is not the load time
        warmup = cornilia_main.CreateModel(provider, {}, model_name=model_name, system_prompt=PLAIN_PROMPT,
                                           response_configs=ResponseConfigs(base_configs, False))
        warmup.tool_tracer = None
        warmup.MultiShotMessage("Reply with the single word: ready")
    except (HarnessBaseError, openai.OpenAIError) as e:
        raise SystemExit(f"error: the warm-up request to {model_name} at {SafeUrl(configs.base_url)} failed: {e}\n"
                         f"Check that the server is running and the model is pulled.")
    print(f"warm-up {time.perf_counter() - warmup_started:.1f}s")

    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    runs, interrupted = [], False
    try:
        for number, (task, repeat, thinking) in enumerate(plan, 1):
            run = RunOne(cornilia_main, provider, model_name, base_configs, task, repeat, thinking)
            runs.append(run)
            print(f"[{number}/{len(plan)}] {task.id}{' +thinking' if thinking else ''} #{repeat} "
                  f"{'PASS' if run['passed'] else 'FAIL'} {run['latency_s']:.1f}s"
                  + (f"  {run['notes'][:120]}" if run["notes"] and not run["passed"] else ""))
    except KeyboardInterrupt:
        interrupted = True
        print("interrupted, writing the runs so far")
    if not runs:
        return

    cornilia_info = CorniliaInfo()
    ollama = OllamaInfo(configs.base_url, model_name) if IsOllama(configs.name, configs.base_url) else None
    request_sampling = {key: value for key, value in base_configs.items() if key in SAMPLING_KEYS}
    sampling = {**((ollama or {}).get("sampling") or {}), **request_sampling} or None
    used_configs = ResponseConfigs(base_configs, args.thinking == "on")
    thinking_configs = ResponseConfigs(base_configs, True)
    meta = {
        "date": started_at.strftime("%Y-%m-%d"), "cornilia": cornilia_info,
        "model": {"name": model_name, "tag": None, "parameters": (ollama or {}).get("parameters"),
                  "active_parameters": None, # no OpenAI-compatible server reports this, so it is never filled in
                  "quantization": (ollama or {}).get("quantization"), "ollama_digest": (ollama or {}).get("digest")},
        "runtime": {"name": "ollama" if ollama else configs.name, "version": (ollama or {}).get("version"),
                    "api_base": SafeUrl(configs.base_url)},
        "hardware": HardwareInfo(), "context_length": (ollama or {}).get("context_length"), "sampling": sampling,
        "settings": {"max_tokens": used_configs.get("max_tokens"), "thinking": args.thinking,
                     "reasoning_effort": used_configs.get("reasoning_effort"),
                     # with compare, the coding and reasoning reruns use these
                     "max_tokens_thinking": thinking_configs.get("max_tokens") if args.thinking == "compare" else None,
                     "max_steps": settings.MAX_STEPS,
                     "retries": settings.MAX_RETRIES, "timeout": configs.timeout},
        "suite": args.suite + (f" ({','.join(only)})" if only else ""), "repeats": repeats, "streamed": provider.stream,
        "duration_s": round(time.perf_counter() - started, 1), "interrupted": interrupted,
    }
    meta["model"]["tag"] = ModelTag(model_name, meta["model"]["quantization"])
    summary = Summarize(runs)
    folder = RunFolder(args.out, meta["date"], meta["model"]["tag"], cornilia_info)
    WriteResults(folder, meta, summary, runs)

    print()
    print(ConsoleTable(summary))
    print()
    for line in Headlines(summary):
        print(line)
    print(f"\n{summary['passed']}/{summary['runs']} runs passed in {meta['duration_s'] / 60:.1f} min, results in {folder}")

if __name__ == "__main__":
    main()
