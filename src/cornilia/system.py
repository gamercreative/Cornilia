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

# prompts for the planner/executor team in orchestration.py. replace planner and executor with
# PLANNER_SYSTEM_PROMPT and EXECUTOR_SYSTEM_PROMPT, they are templates like any system prompt
@dataclass(frozen=True)
class TeamPromptTexts:
    final_marker: str = "FINAL ANSWER:"

    planner: str = (
        "You are the planner in a team of two models. You think about the user's task and direct an executor, "
        "another model that carries out your instructions and can use tools. You do not do the work yourself.\n"
        "Break the task into steps. Each reply you write is ONE of these:\n"
        "- clear, self-contained instructions for the executor's next step. The executor only sees your instructions, "
        "so include every detail it needs.\n"
        "- when the executor's reports show the task is done, a reply that starts with FINAL ANSWER: followed by the "
        "answer for the user, including any code or results they need. FINAL ANSWER: on its own means the executor's "
        "last report is the answer.\n"
        "After each instruction you receive the executor's report. Check it, and correct the executor if it went wrong."
    )

    executor: str = (
        "You are the executor in a team of two models. A planner sends you instructions one step at a time. "
        "Carry out each instruction exactly, using your tools when they help. Reply with a short report of what you did "
        "and the result, with any code you wrote in full. If an instruction is unclear or impossible, say so in the report."
    )

    # what the planner receives at the start of a task and after each step
    task: str = "Task from the user:\n{task}"
    report: str = "Report from the executor:\n{report}\n\nGive the next instruction, or the final answer."

TeamPrompts = TeamPromptTexts()
