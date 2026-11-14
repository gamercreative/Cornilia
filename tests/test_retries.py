# tests for the retry loop in OpenAIModel.CallModel and the wait time in OpenAIModel.RetryWait
# the provider is scripted and time.sleep is replaced, so no server is needed and no test waits for real
import math

import httpx2 as httpx # the venv's openai (3.x) builds its errors on httpx2, not httpx
import openai
import pytest
from openai.types.chat import ChatCompletion

import cornilia.models
from cornilia.context_managers import CacheModelContextManager
from cornilia.errors import RetriesExhaustedError
from cornilia.models import OpenAIModel


def Completion(content=None, tool_calls=None, finish_reason="stop") -> ChatCompletion:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    })


# a provider that answers with scripted replies, a reply can be an exception to raise, or a function of the model name and the messages
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
        if isinstance(reply, BaseException):
            raise reply
        return reply(model_name, prompt) if callable(reply) else reply


def Model(provider, max_retries=1, retry_max_wait=30):
    return OpenAIModel("test-model", provider=provider, context_manager=CacheModelContextManager("system"),
                       tools={}, response_configs={}, max_retries=max_retries, retry_max_wait=retry_max_wait)


def RateLimit(headers=None) -> openai.RateLimitError:
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "http://x"))
    return openai.RateLimitError("slow down", response=response, body=None)


def ConnectionDropped() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://x"))


def AuthFailed() -> openai.AuthenticationError:
    response = httpx.Response(401, request=httpx.Request("POST", "http://x"))
    return openai.AuthenticationError("bad key", response=response, body=None)


# replaces time.sleep for the test, every wait is recorded instead of slept
@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(cornilia.models.time, "sleep", recorded.append)
    return recorded


def test_retry_then_success(waits):
    provider = ScriptedProvider("p", [ConnectionDropped(), Completion("ok")])
    model = Model(provider, max_retries=2)

    assert model.MultiShotMessage("hi") == "ok"
    assert len(provider.calls) == 2
    assert waits == [2.0] # the first retry is 2**1 seconds of backoff


def test_retry_after_header_is_used(waits):
    provider = ScriptedProvider("p", [RateLimit({"retry-after": "7"}), Completion("ok")])

    assert Model(provider, max_retries=2).MultiShotMessage("hi") == "ok"
    assert waits == [7.0]


def test_retry_after_capped_by_retry_max_wait(waits):
    provider = ScriptedProvider("p", [RateLimit({"retry-after": "120"}), Completion("ok")])

    assert Model(provider, max_retries=2, retry_max_wait=30).MultiShotMessage("hi") == "ok"
    assert waits == [30]


def test_retry_after_negative_is_zero(waits):
    provider = ScriptedProvider("p", [RateLimit({"retry-after": "-5"}), Completion("ok")])

    assert Model(provider, max_retries=2).MultiShotMessage("hi") == "ok"
    assert waits == [0]


def test_retry_after_date_falls_back_to_backoff(waits):
    provider = ScriptedProvider("p", [RateLimit({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}), Completion("ok")])

    assert Model(provider, max_retries=2).MultiShotMessage("hi") == "ok"
    assert waits == [2]


def test_retry_after_nan_does_not_crash(waits):
    # float("nan") parses, so the header is not rejected, and the wait must still be a finite number inside the cap
    provider = ScriptedProvider("p", [RateLimit({"retry-after": "nan"}), Completion("ok")])

    assert Model(provider, max_retries=2, retry_max_wait=30).MultiShotMessage("hi") == "ok"
    assert len(waits) == 1
    wait = waits[0]
    assert math.isfinite(wait)
    assert 0 <= wait <= 30


def test_retries_exhausted(waits):
    provider = ScriptedProvider("p", [ConnectionDropped(), ConnectionDropped()])
    model = Model(provider, max_retries=1)

    with pytest.raises(RetriesExhaustedError) as error:
        model.MultiShotMessage("hi")

    assert error.value.attempts == 2
    assert len(provider.calls) == 2
    assert waits == [2]
    assert model.context_manager.GetConversationLength() == 1 # only the system prompt, the failed turn is rolled back


def test_non_retryable_error_not_retried(waits):
    provider = ScriptedProvider("p", [AuthFailed(), Completion("never reached")])
    model = Model(provider, max_retries=3)

    with pytest.raises(openai.AuthenticationError):
        model.MultiShotMessage("hi")

    assert len(provider.calls) == 1
    assert waits == []
    assert model.context_manager.GetConversationLength() == 1
