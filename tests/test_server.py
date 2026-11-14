# tests for the HTTP API. the provider is scripted and every module level state is replaced per test,
# so no server is started and no network call is made
import openai
import pytest

# the server extra and httpx (for TestClient) are optional, so `pip install -e ".[dev]"` alone still runs the rest
pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from openai.types.chat import ChatCompletion  # noqa: E402

import cornilia.settings  # noqa: E402
from cornilia import server  # noqa: E402


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
    def __init__(self, name, replies, unavailable=()):
        self.name = name
        self.replies = list(replies)
        self.available_models = []
        self.unavailable = set(unavailable)
        self.calls = [] # (model_name, messages, tools) for every request

    def IsModelAvailable(self, model_name):
        return model_name not in self.unavailable

    def OneShotMessage(self, prompt, model_name, tools, response_configs):
        self.calls.append((model_name, [dict(message) for message in prompt], tools))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(model_name, prompt) if callable(reply) else reply


def ConnectionFailure() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cornilia.settings, "MODEL_NAME", "test-model")
    monkeypatch.setattr(server, "tools", {})
    monkeypatch.setattr(server, "system_prompt_template", "system")
    monkeypatch.setattr(server, "conversations", {})
    monkeypatch.setattr(server, "conversation_locks", {})
    return TestClient(server.app)


def UseProvider(monkeypatch, provider):
    monkeypatch.setattr(server, "provider", provider)
    return provider


def test_one_shot_returns_reply(client, monkeypatch):
    UseProvider(monkeypatch, ScriptedProvider("p", [Completion("hello")]))

    response = client.post("/one-shot", json={"message": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["response"] == "hello"
    assert body["model_name"] == "test-model"


def test_one_shot_rejects_empty_message(client, monkeypatch):
    UseProvider(monkeypatch, ScriptedProvider("p", []))

    response = client.post("/one-shot", json={"message": ""})

    assert response.status_code == 422


def test_multi_shot_creates_then_continues(client, monkeypatch):
    UseProvider(monkeypatch, ScriptedProvider("p", [Completion("first"), Completion("second")]))

    first = client.post("/multi-shot", json={"message": "a"})
    assert first.status_code == 200
    first_body = first.json()
    conversation_hash = first_body["conversation_hash"]
    assert conversation_hash
    assert first_body["message_count"] == 3 # system, user, assistant

    second = client.post("/multi-shot", json={"message": "b", "conversation_hash": conversation_hash})
    assert second.status_code == 200
    assert second.json()["message_count"] == 5
    assert second.json()["conversation_hash"] == conversation_hash

    history = client.get(f"/multi-shot/{conversation_hash}")
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert len(messages) == 5
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user", "assistant"]


def test_unknown_conversation_hash_404(client, monkeypatch):
    UseProvider(monkeypatch, ScriptedProvider("p", []))

    post = client.post("/multi-shot", json={"message": "a", "conversation_hash": "nope"})
    assert post.status_code == 404

    get = client.get("/multi-shot/nope")
    assert get.status_code == 404


def test_provider_failure_on_new_conversation_drops_it(client, monkeypatch):
    monkeypatch.setattr(cornilia.settings, "MAX_RETRIES", 0)
    UseProvider(monkeypatch, ScriptedProvider("p", [ConnectionFailure()]))

    response = client.post("/multi-shot", json={"message": "a"})

    assert response.status_code == 502
    assert server.conversations == {}
    assert server.conversation_locks == {}


def test_provider_failure_keeps_existing_history(client, monkeypatch):
    monkeypatch.setattr(cornilia.settings, "MAX_RETRIES", 0)
    UseProvider(monkeypatch, ScriptedProvider("p", [Completion("first"), ConnectionFailure()]))

    created = client.post("/multi-shot", json={"message": "a"})
    assert created.status_code == 200
    conversation_hash = created.json()["conversation_hash"]

    failed = client.post("/multi-shot", json={"message": "b", "conversation_hash": conversation_hash})
    assert failed.status_code == 502

    history = client.get(f"/multi-shot/{conversation_hash}")
    assert history.status_code == 200
    assert [message["role"] for message in history.json()["messages"]] == ["system", "user", "assistant"]


def test_max_steps_status(client, monkeypatch):
    monkeypatch.setattr(cornilia.settings, "MAX_STEPS", 1)
    tool_call = {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
    UseProvider(monkeypatch, ScriptedProvider("p", [
        Completion(tool_calls=[tool_call], finish_reason="tool_calls"),
    ]))

    response = client.post("/one-shot", json={"message": "hi"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "max_steps"
    assert body["response"] is None


def test_unavailable_model_400(client, monkeypatch):
    UseProvider(monkeypatch, ScriptedProvider("p", [], unavailable=["other"]))

    response = client.post("/one-shot", json={"message": "hi", "model_name": "other"})

    assert response.status_code == 400
