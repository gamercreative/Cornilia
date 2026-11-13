from .system import *

class HarnessBaseError(Exception):
    pass

class SchemaParserError(HarnessBaseError):
    def __init__(self, message:str, raw_output:str):
        super().__init__(message)
        self.raw_output = raw_output
        
class MaxTokenError(HarnessBaseError):
    def __init__(self, max_tokens:int):
        super().__init__(f"the reply passed the max token limit of {max_tokens} in the middle of a tool call, raise max_tokens (CORNILIA_MAX_TOKENS)")
        self.max_tokens = max_tokens
        
# raised when two tools folders define a tool with the same name
class DuplicateToolError(HarnessBaseError):
    def __init__(self, tool_name:str, first_file:str, second_file:str):
        super().__init__(f"two tools are named {tool_name}: one in {first_file} and one in {second_file}. "
                         f"Rename one of them, or load only one of the folders")
        self.tool_name = tool_name
        self.first_file = first_file
        self.second_file = second_file

class ToolCallError(HarnessBaseError):
    def __init__(self, message:str, tool_name:str,  exit_code:int):
        super().__init__(f"Tool {tool_name} returned an error code of {exit_code}. Error: {message}")
        self.tool_name = tool_name
        self.exit_code = exit_code

# raised when the agent loop hits its step limit, for example a model that keeps asking for tools
class MaxStepsError(HarnessBaseError):
    def __init__(self, max_steps:int):
        super().__init__(f"stopped after {max_steps} steps without a final answer")
        self.max_steps = max_steps

# raised when the provider blocks the response with its content filter
class ContentFilterError(HarnessBaseError):
    def __init__(self):
        super().__init__("the provider's content filter blocked the response")

# raised when a call to the provider keeps failing after every retry
class RetriesExhaustedError(HarnessBaseError):
    def __init__(self, attempts:int, last_error:Exception):
        super().__init__(f"model call failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error

# raised when the provider sends back a response the harness cannot use
class ProviderResponseError(HarnessBaseError):
    pass

# raised when the planner and executor use up their rounds without a final answer
class MaxRoundsError(HarnessBaseError):
    def __init__(self, max_rounds:int, last_report:str | None = None):
        super().__init__(f"the planner gave no final answer within {max_rounds} rounds")
        self.max_rounds = max_rounds
        self.last_report = last_report # the executor's last report, so the work done so far is not lost
