from .providers import *
from .system import *
from .errors import *
from typing import Callable
import json
import time

import openai

# errors worth trying again. a bad request or a wrong api key fails the same way every time so those are not retried
RETRYABLE_ERRORS = (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)

# prints one line per tool call, pass it as tool_tracer to see every step of the loop
def PrintToolTrace(name:str, arguments, result:str, seconds:float):
    print(f"{SystemResponseTypes.SystemInfo}: tool {name}({arguments}) -> {result} [{seconds:.2f}s]")

class OpenAIModel():
    def __init__(self, model_name:str, provider:OpenAIProvider, context_manager, tools: dict , response_configs:dict,
                 max_steps:int = 10, max_retries:int = 1, tool_tracer:Callable | None = None):
        self.provider = provider
        self.model_name = model_name
        self.response_configs = response_configs
        self.context_manager = context_manager
        self.tools = tools
        self.api_tools = [tool.get_schema() for tool in tools.values()]
        self.max_steps = max(max_steps, 1) # model responses allowed in one turn, counting tool rounds and length continuations
        self.max_retries = max(max_retries, 0) # extra attempts for a provider call that failed with a retryable error
        self.tool_tracer = tool_tracer # optional hook called after every tool call, see PrintToolTrace

    def RunTool(self,tool_call) -> str:
        started = time.perf_counter()
        tool_name = getattr(getattr(tool_call, "function", None), "name", "unknown")
        tool_args = getattr(getattr(tool_call, "function", None), "arguments", None)

        try:
            if tool_name not in self.tools:
                raise HarnessBaseError(f"no tool named {tool_name}, available tools: {list(self.tools)}")
            tool_args = json.loads(tool_args or "{}")
            result = json.dumps(self.tools[tool_name].ToolFunction(**tool_args), default=str) # dates, decimals, paths etc are sent as text

        except Exception as e:
            result = f"{SystemResponseTypes.SystemError}: tool {tool_name} failed: {e}"

        if self.tool_tracer:
            self.tool_tracer(tool_name, tool_args, result, time.perf_counter() - started)
        return result

    # clears the conversation, the history lives in the context manager
    def ResetModelMessageQueue(self):
        self.context_manager.ResetConversation()

    # sends the current history to the provider, retrying a bounded number of times on errors that can pass
    def CallModel(self) -> ChatCompletion:
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.api_tools, self.response_configs)
            except RETRYABLE_ERRORS as e:
                if attempt == attempts:
                    raise RetriesExhaustedError(attempts, e) from e
                print(f"{SystemResponseTypes.SystemWarning}: model call failed ({e}), retrying {attempt}/{self.max_retries}")
                time.sleep(attempt)
        raise HarnessBaseError("unreachable") # keeps type checkers happy, the loop always returns or raises

    # the agent loop: handles a response, runs any tools it asks for and calls the model again until it gives a final answer
    def HandleModelResponse(self, resp) -> str:
        parts = [] # text from replies cut off by the token limit, joined into the final answer
        steps = 1

        while True:
            if not isinstance(resp, ChatCompletion):
                raise ProviderResponseError("the provider response is not a ChatCompletion")
            if not resp.choices:
                raise ProviderResponseError("the provider returned a response with no choices")

            response = resp.choices[0]
            message = response.message
            response_finish_reason = response.finish_reason

            # decide on tool calls from the message itself, not every server sets finish_reason to tool_calls
            if message.tool_calls:
                # out of steps, don't run tools whose results the model would never see
                if steps >= self.max_steps:
                    raise MaxStepsError(self.max_steps)
                self.context_manager.AppendToolCallMessage(message)

                for tool_call in message.tool_calls:
                    result = self.RunTool(tool_call)
                    self.context_manager.AppendToolResult(tool_call.id, result)

            # the reply hit the max tokens limit, keep the partial text and ask the model to continue it
            elif response_finish_reason == OpenAIFinishReason.LENGTH:
                parts.append(message.content or "")
                self.context_manager.AppendAssistantMessage(message.content or "")
                self.context_manager.AppendUserMessage(AgentErrorPrompts.length_exceeded) # a user turn, some chat templates reject a system message after the first one

            elif response_finish_reason == OpenAIFinishReason.CONTENT:
                raise ContentFilterError()

            # stop, or a server that sends no finish reason or its own name for a normal end
            else:
                text_response = message.content or ""
                self.context_manager.AppendAssistantMessage(text_response)
                return "".join(parts) + text_response

            if steps >= self.max_steps:
                raise MaxStepsError(self.max_steps)

            resp = self.CallModel()
            steps += 1

    # a function to handle the multi shot messages sent to the model as to retain history etc
    # if the turn fails the history is put back to how it was before the prompt, and the error is raised
    def MultiShotMessage(self, prompt:str) -> str:
        history_length = self.context_manager.GetConversationLength()
        self.context_manager.AppendUserMessage(prompt)

        try:
            return self.HandleModelResponse(self.CallModel())
        except Exception:
            self.context_manager.TruncateConversation(history_length)
            raise

    MutliShotMessage = MultiShotMessage # the old misspelled name, kept so existing code keeps working
