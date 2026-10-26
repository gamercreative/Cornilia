from system import *

class HarnessBaseError(Exception):
    pass

class SchemaParserError(HarnessBaseError):
    def __init__(self, message:str, raw_output:str):
        super().__init__(message)
        self.raw_output = raw_output
        
class MaxTokenError(HarnessBaseError):
    def __init__(self, max_tokens:int):
        super().__init__(f"surpassed max token length of {max_tokens}")
        self.max_tokens = max_tokens
        
class ToolCallError(HarnessBaseError):
    def __init__(self, message:str, tool_name:str,  exit_code:int):
        super().__init__(f"Tool {tool_name} returned an error code of {exit_code}. Error: {message}")
        self.tool_name = tool_name
        self.exit_code = exit_code