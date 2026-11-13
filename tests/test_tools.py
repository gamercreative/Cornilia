# tests for tool schemas, argument handling and the tool call loop. the models are scripted, so no server is needed
from typing import Annotated, Literal, TypedDict

import pytest

from cornilia.errors import MaxTokenError
from cornilia.tools import BuildToolRegistry, tool

from test_orchestration import Completion, Model, ScriptedProvider


class Filters(TypedDict, total=False):
    labels: list[str]
    state: Literal["open", "closed"]


class Rule(TypedDict):
    name: str
    filters: Filters
    limit: Annotated[int, "How many"]


@tool
def apply_rules(rules: list[Rule], dry_run: bool = True, limit: int = 5) -> dict:
    """Applies rules."""
    return {"rules": rules, "dry_run": dry_run, "limit": limit}


def test_typeddict_becomes_nested_object_schema():
    rules = apply_rules.get_schema()["function"]["parameters"]["properties"]["rules"]
    rule = rules["items"]
    assert rules["type"] == "array"
    assert rule["type"] == "object"
    assert rule["required"] == ["name", "filters", "limit"]
    assert rule["properties"]["limit"] == {"type": "integer", "description": "How many"}
    filters = rule["properties"]["filters"]
    assert "required" not in filters # total=False, every field is optional
    assert filters["properties"]["labels"] == {"type": "array", "items": {"type": "string"}}
    assert filters["properties"]["state"]["enum"] == ["open", "closed"]


def test_arguments_are_coerced_to_their_hints():
    result = apply_rules.ToolFunction(rules='[{"name": "a", "filters": {}, "limit": 1}]', dry_run="false", limit="7")
    assert result == {"rules": [{"name": "a", "filters": {}, "limit": 1}], "dry_run": False, "limit": 7}


def test_null_for_a_defaulted_argument_uses_the_default():
    assert apply_rules.ToolFunction(rules=[], dry_run=None, limit=None) == {"rules": [], "dry_run": True, "limit": 5}


def test_unclear_values_are_passed_on_unchanged():
    assert apply_rules.ToolFunction(rules=[], limit="seven")["limit"] == "seven"


def ToolCall(arguments, call_id="call_1", name="apply_rules"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_tool_call_without_id_gets_one():
    provider = ScriptedProvider("p", [
        Completion(tool_calls=[ToolCall('{"rules": []}', call_id="")], finish_reason="tool_calls"),
        Completion("done"),
    ])
    model = Model(provider, "m", BuildToolRegistry([apply_rules]))
    assert model.MultiShotMessage("go") == "done"
    history = model.context_manager.GetConversationHistory()
    call_id = history[2]["tool_calls"][0]["id"]
    assert call_id and history[3]["tool_call_id"] == call_id


def test_tool_call_cut_off_by_the_token_limit_raises():
    provider = ScriptedProvider("p", [Completion(tool_calls=[ToolCall('{"rules": [')], finish_reason="length")])
    model = Model(provider, "m", BuildToolRegistry([apply_rules]))
    with pytest.raises(MaxTokenError):
        model.MultiShotMessage("go")
    assert len(model.context_manager.GetConversationHistory()) == 1 # the turn was rolled back
