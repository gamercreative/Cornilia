# tests for the planner/executor team. the models are scripted, so no server is needed
import json

import pytest
from openai.types.chat import ChatCompletion

from cornilia.context_managers import CacheModelContextManager
from cornilia.errors import HarnessBaseError, MaxRoundsError
from cornilia.models import OpenAIModel
from cornilia.orchestration import EXECUTOR, PLANNER, PlannerExecutor, StripReasoning
from cornilia.tools import BuildToolRegistry, tool


def Completion(content=None, tool_calls=None, finish_reason="stop") -> ChatCompletion:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    })


# a provider that answers with scripted replies, a reply can be a function of the model name and the messages
class ScriptedProvider:
    def __init__(self, name, replies):
        self.name = name
        self.replies = list(replies)
        self.available_models = []
        self.calls = [] # (model_name, messages, tools) for every request

    def IsModelAvailable(self, model_name):
        return True

    def OneShotMessage(self, prompt, model_name, tools, response_configs):
        self.calls.append((model_name, [dict(message) for message in prompt], tools))
        reply = self.replies.pop(0)
        return reply(model_name, prompt) if callable(reply) else reply


def Model(provider, model_name, tools=None):
    return OpenAIModel(model_name, provider=provider, context_manager=CacheModelContextManager("system"),
                       tools=tools or {}, response_configs={}, max_retries=0)


@tool
def add(a: int, b: int) -> int:
    """Adds two numbers."""
    return a + b


def test_planner_instructs_executor_then_answers():
    planner_provider = ScriptedProvider("p", [
        Completion("<think>the user wants a sum</think>Use the add tool on 2 and 3."),
        Completion("The executor is done.\n**FINAL ANSWER:** 2 + 3 = 5"),
    ])
    executor_provider = ScriptedProvider("e", [
        Completion(tool_calls=[{"id": "c1", "type": "function", "function": {"name": "add", "arguments": json.dumps({"a": 2, "b": 3})}}],
                   finish_reason="tool_calls"),
        Completion("I called add and got 5."),
    ])
    steps = []
    team = PlannerExecutor(Model(planner_provider, "thinker"), Model(executor_provider, "coder", BuildToolRegistry([add])),
                           step_tracer=lambda role, model_name, text: steps.append((role, model_name, text)))

    result = team.Run("what is 2 + 3?")

    assert result.answer == "2 + 3 = 5"
    assert result.rounds == 2
    assert result.last_report == "I called add and got 5."
    # the executor sees the instruction without the planner's thinking
    executor_messages = executor_provider.calls[0][1]
    assert executor_messages[-1] == {"role": "user", "content": "Use the add tool on 2 and 3."}
    # the planner never gets tools, the executor does
    assert planner_provider.calls[0][2] == []
    assert executor_provider.calls[0][2][0]["function"]["name"] == "add"
    # the planner reads the executor's report
    assert "I called add and got 5." in planner_provider.calls[1][1][-1]["content"]
    assert [step[:2] for step in steps] == [(PLANNER, "thinker"), (EXECUTOR, "coder")]


def test_bare_final_marker_returns_the_last_report():
    planner = Model(ScriptedProvider("p", [Completion("write hello world in python"), Completion("FINAL ANSWER:")]), "thinker")
    executor = Model(ScriptedProvider("e", [Completion("print('hello world')")]), "coder")

    assert PlannerExecutor(planner, executor).Run("hello world please").answer == "print('hello world')"


def test_running_out_of_rounds_rolls_back_both_conversations():
    planner = Model(ScriptedProvider("p", [Completion("step one"), Completion("step two")]), "thinker")
    executor = Model(ScriptedProvider("e", [Completion("did one"), Completion("did two")]), "coder")
    team = PlannerExecutor(planner, executor, max_rounds=2)

    with pytest.raises(MaxRoundsError) as error:
        team.Run("never ends")

    assert error.value.last_report == "did two"

    assert planner.context_manager.GetConversationLength() == 1 # only the system prompt
    assert executor.context_manager.GetConversationLength() == 1


def test_models_can_be_changed_and_swapped():
    shared = ScriptedProvider("shared", [
        Completion("instructions"), Completion("report"), Completion("FINAL ANSWER: done"),
    ])
    other = ScriptedProvider("other", [])
    team = PlannerExecutor(Model(shared, "deep-thinker"), Model(shared, "fast-coder"))

    team.SwapModels()
    assert (team.planner.model_name, team.executor.model_name) == ("fast-coder", "deep-thinker")

    team.SetRoleModel(EXECUTOR, "another-coder")
    team.SetRoleModel(PLANNER, "remote-thinker", provider=other)
    assert team.planner.provider is other
    team.SetRoleModel(PLANNER, "fast-coder", provider=shared)

    team.Run("task")
    assert [call[0] for call in shared.calls] == ["fast-coder", "another-coder", "fast-coder"]

    with pytest.raises(HarnessBaseError):
        team.SetRoleModel("reviewer", "x")


def test_strip_reasoning():
    assert StripReasoning("<think>a\nb</think>\nanswer") == "answer"
    assert StripReasoning("answer <think>cut off mid thought") == "answer"
    assert StripReasoning("no thinking here") == "no thinking here"
