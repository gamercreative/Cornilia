import threading
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict, Field

from errors import *
from main import CreateModel, CreateProvider, CreateTools, LoadSystemPrompt
from models import OpenAIModel

app = FastAPI()

"""
    endpoints go here and are served thorugh this endpoint
    run from this folder with `uvicorn server:app`, or from anywhere with `uvicorn --app-dir <path to Cornilia> server:app`
"""

provider = CreateProvider()
tools = CreateTools()
# read once at startup, so a missing prompt file stops the server here instead of failing every request
system_prompt_template = LoadSystemPrompt()

# conversation_hash -> model holding its own cache context manager. kept in memory, so a restart forgets every conversation
conversations: dict[str, OpenAIModel] = {}
conversation_locks: dict[str, threading.Lock] = {}
conversations_lock = threading.Lock()

Status = Literal["ok", "content_filter", "max_steps"]


# ---- json contract ----
class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow") # tool call messages carry extra provider fields

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class OneShotRequest(BaseModel):
    message: str = Field(min_length=1)
    model_name: str | None = None
    system_prompt: str | None = None # replaces the configured system prompt for this request


class OneShotResponse(BaseModel):
    model_name: str
    status: Status
    response: str | None


class MultiShotRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_hash: str | None = None  # omit to start a new conversation
    model_name: str | None = None  # only used when starting a new conversation


class MultiShotResponse(BaseModel):
    conversation_hash: str
    model_name: str
    status: Status
    response: str | None
    message_count: int


class ConversationResponse(BaseModel):
    conversation_hash: str
    model_name: str
    messages: list[ChatMessage]


# ---- helpers ----
def NewModel(model_name:str | None, system_prompt:str | None = None) -> OpenAIModel:
    try:
        model = CreateModel(provider, tools, model_name=model_name, system_prompt=system_prompt or system_prompt_template)
    except HarnessBaseError as e:
        raise HTTPException(400, str(e))
    if not provider.IsModelAvailable(model.model_name):
        raise HTTPException(400, f"model '{model.model_name}' not available, use one of {provider.available_models}")
    return model


def GetOrCreateConversation(conversation_hash:str | None, model_name:str | None) -> tuple[str, OpenAIModel, threading.Lock]:
    if conversation_hash is None:
        model = NewModel(model_name) # built outside the lock, it can read the system prompt file
        with conversations_lock:
            conversation_hash = uuid.uuid4().hex
            conversations[conversation_hash] = model
            conversation_locks[conversation_hash] = threading.Lock()
            return conversation_hash, model, conversation_locks[conversation_hash]

    with conversations_lock:
        if conversation_hash not in conversations:
            raise HTTPException(404, f"conversation '{conversation_hash}' not found")
        return conversation_hash, conversations[conversation_hash], conversation_locks[conversation_hash]


# runs one turn and turns the expected outcomes into a status, history is already rolled back by the model on failure
def RunTurn(model:OpenAIModel, message:str) -> tuple[Status, str | None]:
    try:
        return "ok", model.MultiShotMessage(message)
    except ContentFilterError:
        return "content_filter", None
    except MaxStepsError:
        return "max_steps", None
    except (RetriesExhaustedError, ProviderResponseError, OpenAIError) as e:
        raise HTTPException(502, f"model provider error: {e}")
    except HarnessBaseError as e:
        raise HTTPException(500, str(e))


# ---- endpoints ----
# this endpoint is to get the conversation_history based on hash
@app.get("/multi-shot/{conversation_hash}", response_model=ConversationResponse)
def get_conversation(conversation_hash:str):
    with conversations_lock:
        model = conversations.get(conversation_hash)
        lock = conversation_locks.get(conversation_hash)
    if model is None or lock is None:
        raise HTTPException(404, f"conversation '{conversation_hash}' not found")

    with lock: # wait for a turn in progress so the history is never read half written
        messages = list(model.context_manager.GetConversationHistory())
    return ConversationResponse(conversation_hash=conversation_hash, model_name=model.model_name, messages=messages)


# this endpoint is to append to the conversation (creates one if no hash is given)
@app.post("/multi-shot", response_model=MultiShotResponse)
def continue_conversation(body:MultiShotRequest):
    conversation_hash, model, lock = GetOrCreateConversation(body.conversation_hash, body.model_name)

    with lock:  # one in-flight message per conversation so history stays ordered
        try:
            status, response = RunTurn(model, body.message)
        except Exception: # any failure, not only the expected ones turned into an HTTP error
            if body.conversation_hash is None: # the caller never got this hash, so drop the empty conversation
                with conversations_lock:
                    conversations.pop(conversation_hash, None)
                    conversation_locks.pop(conversation_hash, None)
            raise
        return MultiShotResponse(
            conversation_hash=conversation_hash,
            model_name=model.model_name,
            status=status,
            response=response,
            message_count=model.context_manager.GetConversationLength(),
        )


# this endpoint is to one shot messages without hisotry without memory
@app.post("/one-shot", response_model=OneShotResponse)
def create_conversation(body:OneShotRequest):
    model = NewModel(body.model_name, body.system_prompt) # a fresh model and context manager, so nothing is remembered
    status, response = RunTurn(model, body.message)
    return OneShotResponse(model_name=model.model_name, status=status, response=response)


# lets you run the server with `python server.py`
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
