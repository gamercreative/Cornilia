from enum import Enum
from dataclasses import dataclass

# system is the system that contacts the ai model

class SystemResponseTypes(str, Enum):
    SystemWarning = "System_Warning"
    SystemNotice = "System_Notice"
    SystemError = "System_Error"
    SystemInfo = "System_Info"

class Role(str, Enum):
    USER = "user"
    SYSTEM = "system"
    ASSISTANT = "assistant"
    TOOL = "tool"
    
class OpenAIFinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT = "content_filter"
    
# this is a list of errors that the 
@dataclass(frozen=True)
class AgentErrorPrompts:
    # if the return token length of hte model exceeds max length
    length_exceeded: str = f"{SystemResponseTypes.SystemError}: MAX_TOKENS_REACHED. Continue the previous output string exactly from the point of interruption. DO NOT output introductory text."