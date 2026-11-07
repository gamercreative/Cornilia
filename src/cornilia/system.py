from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from string import Template

# system is the system that contacts the ai model

# str enums print their value, so f"{Role.USER}" is "user" on every python version (3.12+ would print "Role.USER")
class StrValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value

class SystemResponseTypes(StrValueEnum):
    SystemWarning = "System_Warning"
    SystemNotice = "System_Notice"
    SystemError = "System_Error"
    SystemInfo = "System_Info"

class Role(StrValueEnum):
    USER = "user"
    SYSTEM = "system"
    ASSISTANT = "assistant"
    TOOL = "tool"
    
class OpenAIFinishReason(StrValueEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT = "content_filter"
    
# prompts the harness sends to the model itself
@dataclass(frozen=True)
class AgentErrorPrompts:
    # sent when a reply hits the max tokens limit, so the model continues it
    length_exceeded: str = f"{SystemResponseTypes.SystemError}: MAX_TOKENS_REACHED. Continue the previous output string exactly from the point of interruption. DO NOT output introductory text."

# fills $date, $time, $datetime and $weekday (plus any extra values given) into a system prompt
# uses $name placeholders so braces in prompts, like json examples, are left alone. unknown names stay as they are
def RenderSystemPrompt(template:str, values:dict | None = None) -> str:
    now = datetime.now().astimezone()
    runtime_values = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M %Z"),
        "datetime": now.isoformat(timespec="minutes"),
        "weekday": now.strftime("%A"),
        **(values or {}),
    }
    return Template(template).safe_substitute(runtime_values)
