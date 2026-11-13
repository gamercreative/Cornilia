# tests for examples/benchmark: scoring, suite selection and one run through the agent loop. no model or server is needed
import json
import sys
import time
from pathlib import Path

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples" / "benchmark"))

import bench
from bench_scoring import (ABSENT, AllOf, Call, CheckToolCall, CodePasses, CountsAre, JsonMatches, LeakedToolCall, Like,
                           Mentions, NoCalls, NumberIs, OneOf)
from bench_tasks import CATEGORIES, TASKS, THINKING_CATEGORIES, SelectTasks, ToolRegistry

SCHEMAS = {name: item.get_schema() for name, item in ToolRegistry().items()}


# --- suites ---

def test_quick_suite_is_small_and_skips_the_slow_categories():
    tasks = SelectTasks("quick")
    assert 12 <= len(tasks) <= 15
    categories = {task.category for task in tasks}
    assert "long_context" not in categories and "speed" not in categories
    assert {"simple", "chain", "nested", "filters", "json", "reasoning"} <= categories


def test_full_suite_has_every_task_and_category():
    tasks = SelectTasks("full")
    assert tasks == TASKS
    assert {task.category for task in tasks} == set(CATEGORIES)
    assert len({task.id for task in tasks}) == len(tasks)


def test_only_filters_categories_and_rejects_unknown_ones():
    assert {task.category for task in SelectTasks("full", ["filters", "json"])} == {"filters", "json"}
    with pytest.raises(ValueError, match="unknown categories"):
        SelectTasks("quick", ["nope"])
    with pytest.raises(ValueError, match="--suite full"):
        SelectTasks("quick", ["long_context"])


def test_plan_repeats_tasks_but_runs_long_context_once():
    tasks = SelectTasks("full", ["simple", "long_context"])
    plan = bench.Plan(tasks, 3, "off")
    simple = [item for item in plan if item[0].category == "simple"]
    long_context = [item for item in plan if item[0].category == "long_context"]
    assert len(simple) == 3 * len([task for task in tasks if task.category == "simple"])
    assert len(long_context) == 12 and all(repeat == 1 for _, repeat, _ in long_context)
    assert not any(thinking for _, _, thinking in plan)


def test_thinking_compare_reruns_only_coding_and_reasoning():
    tasks = SelectTasks("full", ["simple", "reasoning"])
    plan = bench.Plan(tasks, 1, "compare")
    rerun = [task.category for task, _, thinking in plan if thinking]
    assert rerun and set(rerun) <= set(THINKING_CATEGORIES)
    assert len(plan) == len(tasks) + len(rerun)


def test_response_configs_for_thinking():
    base = {"max_tokens": 512, "reasoning_effort": "none"}
    assert bench.ResponseConfigs(base, False) == {"max_tokens": 2048, "reasoning_effort": "none"}
    assert bench.ResponseConfigs(base, True) == {"max_tokens": 8192, "reasoning_effort": "medium"}
    assert bench.ResponseConfigs({}, False) == {} # a hosted provider without max_tokens is left alone
    assert base == {"max_tokens": 512, "reasoning_effort": "none"}


# --- tool call validity ---

def test_valid_call():
    call = CheckToolCall("get_customer", '{"customer_id": "C001"}', SCHEMAS)
    assert call.valid and call.arguments == {"customer_id": "C001"}


@pytest.mark.parametrize("name, arguments, problem", [
    ("get_customer", "{not json", "not valid JSON"),
    ("get_customer", "[1, 2]", "not a JSON object"),
    ("get_customers", '{"customer_id": "C001"}', "no tool named"),
    ("get_customer", "{}", "is required"),
    ("get_customer", '{"customer_id": "C001", "verbose": true}', "unknown argument"),
    ("convert_currency", '{"amount": "10", "from_currency": "USD", "to_currency": "EUR"}', "should be a number"),
    ("list_issues", '{"status": "opened"}', "expected one of"),
    ("create_order", '{"customer_id": "C001", "items": [{"sku": "SKU-101", "quantity": "2"}]}', "should be an integer"),
    ("create_issue", '{"customer_id": "C001", "title": "t", "details": {"priority": "high"}}', "details.channel is required"),
])
def test_invalid_calls(name, arguments, problem):
    call = CheckToolCall(name, arguments, SCHEMAS)
    assert not call.valid
    assert problem in " ".join(call.errors)


def test_null_for_an_optional_argument_is_valid():
    assert CheckToolCall("list_issues", '{"status": null, "priority": "high"}', SCHEMAS).valid


def test_every_expected_call_fits_its_tool():
    def calls_in(matcher):
        if isinstance(matcher, Call):
            yield matcher
        for part in getattr(matcher, "parts", ()) + getattr(matcher, "options", ()):
            yield from calls_in(part)

    for task in TASKS:
        for call in calls_in(task.calls):
            arguments = {key: (value.text if isinstance(value, Like) else value) for key, value in call.arguments.items()
                         if value is not ABSENT}
            checked = CheckToolCall(call.name, json.dumps(arguments), SCHEMAS)
            assert not [error for error in checked.errors if "is required" not in error], (task.id, checked.errors)


# --- argument matching ---

def Calls(*pairs):
    return [CheckToolCall(name, json.dumps(arguments), SCHEMAS) for name, arguments in pairs]


def test_call_matches_a_subset_with_lists_in_any_order():
    calls = Calls(("create_order", {"customer_id": "c003", "items": [{"sku": "SKU-204", "quantity": 1}, {"sku": "sku-101", "quantity": 2}]}))
    assert Call("create_order", customer_id="C003", items=[{"sku": "SKU-101", "quantity": 2}, {"sku": "SKU-204", "quantity": 1}]).matches(calls)
    assert not Call("create_order", items=[{"sku": "SKU-101", "quantity": 3}, {"sku": "SKU-204", "quantity": 1}]).matches(calls)


def test_open_and_closed_needs_both_or_no_filter():
    matcher = OneOf(AllOf(Call("list_issues", status="open"), Call("list_issues", status="closed")),
                    Call("list_issues", status=ABSENT, customer_id=ABSENT, priority=ABSENT))
    assert matcher.matches(Calls(("list_issues", {"status": "open"}), ("list_issues", {"status": "closed"})))
    assert matcher.matches(Calls(("list_issues", {})))
    assert not matcher.matches(Calls(("list_issues", {"status": "open"}))) # the "closed" half was dropped


def test_like_name_argument_and_no_calls():
    assert Call("find_customers", name=Like("maya")).matches(Calls(("find_customers", {"name": "Maya Fares"})))
    assert NoCalls().matches([]) and not NoCalls().matches(Calls(("get_customer", {"customer_id": "C001"})))


# --- answer checks ---

def test_number_uses_the_answer_line():
    assert NumberIs(33)("40 minus 25% is 30, plus tax.\nANSWER: 33")[0]
    assert not NumberIs(33)("33 is wrong.\nANSWER: 30")[0]
    assert NumberIs(1240.5)("It is $1,240.50 in total.")[0]


def test_mentions_with_forbidden_values_on_the_answer_line():
    check = Mentions("C003", forbidden=("C001", "C005"), final_only=True)
    assert check("C001 spent 1240.50, C003 spent 1255.00 and C005 1249.75.\nANSWER: C003")[0]
    assert not check("ANSWER: C005")[0]
    assert not check("C001, C003 and C005 are close.")[0] # no answer line, so every id counts


def test_counts_near_their_labels():
    check = CountsAre(open=6, closed=3)
    assert check("There are 6 open issues and 3 closed issues.")[0]
    assert check("Open: 6\nClosed: 3")[0]
    assert not check("There are 6 open issues.")[0]


def test_json_checks():
    assert JsonMatches({"a": 1}, strict=True)('{"a": 1, "b": 2}')[0]
    assert not JsonMatches({"a": 1}, strict=True)('```json\n{"a": 1}\n```')[0]
    passed, note = JsonMatches({"a": 1})('```json\n{"a": 1}\n```')
    assert passed and "fence" in note
    assert not JsonMatches({"a": 1})("<think>hm</think>{\"a\": 2}")[0]


def test_leaked_tool_calls():
    assert LeakedToolCall('<tool_call>{"name": "get_customer"}</tool_call>', ["get_customer"])
    assert LeakedToolCall('{"name": "get_customer", "arguments": {"customer_id": "C1"}}', ["get_customer"])
    assert not LeakedToolCall("Customer C001 lives in Beirut.", ["get_customer"])


# --- coding ---

ADD = CodePasses("add", ["FUNCTION(2, 3) == 5"], timeout=5)


def test_code_passes_and_fails():
    assert ADD("```python\ndef add(a, b):\n    return a + b\n```") == (True, "")
    passed, note = ADD("```python\ndef add(a, b):\n    return a - b\n```")
    assert not passed and "FUNCTION(2, 3) == 5" in note


@pytest.mark.parametrize("code", [
    "import os\ndef add(a, b):\n    return a + b",
    "def add(a, b):\n    return a + b\nprint('hi')",
    "def add(a, b):\n    return open('x').read()",
    "def add(a, b):\n    return ().__class__",
])
def test_unsafe_code_is_not_run(code):
    passed, note = ADD(code)
    assert not passed and note.startswith("code not run")


def test_code_that_hangs_is_stopped():
    passed, note = CodePasses("add", ["FUNCTION(1, 2) == 3"], timeout=1)("def add(a, b):\n    while True:\n        pass")
    assert not passed and "stopped" in note


# --- one run through Cornilia's agent loop, with a scripted provider ---

def Completion(content=None, tool_calls=None, prompt_tokens=50, completion_tokens=10):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [{"id": f"call_{index}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(arguments)}}
                                 for index, (name, arguments) in enumerate(tool_calls)]
    return ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": 0},
    })


class ScriptedProvider(bench.MeasuredProvider):
    def __init__(self, replies):
        from cornilia.providers import ProviderConfigs
        super().__init__(ProviderConfigs(name="test", base_url="http://localhost:1/v1", api_key="test"))
        self.replies = list(replies)
        self.sent = []

    def StreamedMessage(self, prompt, model_name, tools, response_configs, started):
        self.sent.append(response_configs)
        time.sleep(0.01) # so the reply takes longer than its first token
        return self.replies.pop(0), 0.005


def RunTask(task_id, replies, thinking=False):
    from cornilia import main
    task = next(task for task in TASKS if task.id == task_id)
    provider = ScriptedProvider(replies)
    return bench.RunOne(main, provider, "test-model", {"max_tokens": 512}, task, 1, thinking), provider


def test_run_through_the_agent_loop_passes():
    run, provider = RunTask("simple/customer_city", [Completion(tool_calls=[("get_customer", {"customer_id": "C003"})]),
                                                     Completion("Lea Khoury lives in Byblos.")])
    assert run["passed"] and run["args_ok"] and run["answer_ok"]
    assert (run["calls_valid"], run["calls_total"], run["requests"]) == (1, 1, 2)
    assert run["ttft_s"] == 0.005 and run["completion_tokens"] == 20 and run["tokens_per_s"] is not None
    assert provider.sent[0]["max_tokens"] == 2048


def test_run_with_wrong_arguments_fails():
    run, _ = RunTask("filters/open_and_closed", [Completion(tool_calls=[("list_issues", {"status": "open"})]),
                                                 Completion("There are 6 open issues and 3 closed ones.")])
    assert not run["passed"] and run["answer_ok"] and run["args_ok"] is False
    assert "expected" in run["notes"]


def test_run_with_thinking_sends_the_setting():
    run, provider = RunTask("reasoning/discount_tax", [Completion("ANSWER: 33")], thinking=True)
    assert run["passed"] and run["group"] == "reasoning+thinking"
    assert provider.sent[0]["reasoning_effort"] == "medium" and provider.sent[0]["max_tokens"] == 8192


def test_streamed_tool_calls_are_put_back_together():
    def Chunk(delta, finish_reason=None, usage=None):
        return ChatCompletionChunk.model_validate({
            "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m", "usage": usage,
            "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})

    chunks = [Chunk({"role": "assistant"}),
              # two calls both numbered 0 with their own ids, and arguments in pieces, as some servers send them
              Chunk({"tool_calls": [{"index": 0, "id": "a", "type": "function", "function": {"name": "get_customer", "arguments": '{"custo'}}]}),
              Chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'mer_id": "C001"}'}}]}),
              Chunk({"tool_calls": [{"index": 0, "id": "b", "type": "function", "function": {"name": "get_customer", "arguments": '{"customer_id": "C002"}'}}]}),
              Chunk({}, "tool_calls"),
              Chunk(None, usage={"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52})]

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["stream"] and kwargs["stream_options"] == {"include_usage": True}
                    return iter(chunks)

    from cornilia.providers import ProviderConfigs
    provider = bench.MeasuredProvider(ProviderConfigs(name="test", base_url="http://localhost:1/v1", api_key="test"))
    provider.client = FakeClient()
    response = provider.OneShotMessage([{"role": "user", "content": "hi"}], "m", [], {})
    calls = response.choices[0].message.tool_calls
    assert [(call.id, json.loads(call.function.arguments)) for call in calls] == [("a", {"customer_id": "C001"}), ("b", {"customer_id": "C002"})]
    record = provider.records[0]
    assert record.ttft is not None and record.completion_tokens == 12 and len(record.tool_calls) == 2


# --- results ---

def test_results_folder(tmp_path):
    run, _ = RunTask("simple/customer_city", [Completion(tool_calls=[("get_customer", {"customer_id": "C003"})]),
                                              Completion("Byblos.")])
    failed, _ = RunTask("reasoning/ordering", [Completion("ANSWER: Omar")])
    summary = bench.Summarize([run, failed])
    assert summary["categories"]["simple"]["passed"] == 1 and summary["categories"]["reasoning"]["passed"] == 0
    assert (summary["calls_valid"], summary["calls_total"]) == (1, 1)

    meta = {"date": "2026-11-13", "cornilia": {"version": "0.1.0", "commit": None, "branch": None},
            "model": {"name": "m:1b", "tag": "m-1b", "parameters": None, "active_parameters": None, "quantization": None,
                      "ollama_digest": None},
            "runtime": {"name": "test", "version": None, "api_base": "http://localhost:1/v1"},
            "hardware": {"system": None}, "context_length": None, "sampling": None,
            "settings": {"max_tokens": 2048, "thinking": "off", "max_steps": 10, "retries": 1, "timeout": 120},
            "suite": "quick", "repeats": 1, "streamed": True, "duration_s": 3.0, "interrupted": False}
    folder = bench.RunFolder(tmp_path, meta["date"], meta["model"]["tag"], meta["cornilia"])
    assert folder.name == "2026-11-13_m-1b_cornilia-0.1.0-nocommit"
    bench.WriteResults(folder, meta, summary, [run, failed])
    saved = json.loads((folder / "run.json").read_text())
    assert saved["model"]["ollama_digest"] is None and saved["totals"]["categories"]["simple"]["runs"] == 1
    assert len((folder / "tasks.jsonl").read_text().splitlines()) == 2
    report = (folder / "REPORT.md").read_text()
    assert "## Misses (1)" in report and "reasoning/ordering" in report
    assert bench.RunFolder(tmp_path, meta["date"], meta["model"]["tag"], meta["cornilia"]).name.endswith("_2")


def test_names_and_urls():
    assert bench.ModelTag("qwen3.5:35b-a3b", "Q4_K_M") == "qwen3.5-35b-a3b-q4_K_M"
    assert bench.ModelTag("qwen3.5:35b-a3b-q4_K_M", "Q4_K_M") == "qwen3.5-35b-a3b-q4_K_M"
    assert bench.ModelTag("org/model", None) == "org-model"
    assert bench.SafeUrl("https://user:secret@example.com:8443/v1?key=abc") == "https://example.com:8443/v1"
