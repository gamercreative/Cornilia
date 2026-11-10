import re
from dataclasses import dataclass
from typing import Callable

from .models import OpenAIModel
from .system import *
from .errors import *

"""
    The planner/executor pattern: two models work on one task.

    The planner (also called orchestrator or thinker) reads the task, decides what to do next and writes
    instructions. It never calls tools. The executor carries out each instruction, with tools when it has them,
    and reports back. The planner reads the report and either gives the next instruction or the final answer.

        user task -> planner -> instructions -> executor (+ tools) -> report -> planner -> ... -> final answer

    Each role is an ordinary OpenAIModel with its own conversation, so either role can be any model on any
    OpenAI-compatible provider, and a role's model can be changed between tasks with SetRoleModel or SwapModels.
"""

PLANNER = "planner"
EXECUTOR = "executor"

# reasoning models such as deepseek-r1 may put their thinking inside <think>...</think> in the reply text.
# it is useful to read, but the other role only needs the conclusion
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

def StripReasoning(text:str) -> str:
    text = THINK_BLOCK.sub("", text)
    # a reply cut off mid-thought has an opening tag and no closing one, keep what came before it
    if "<think>" in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()

# what one task produced: the final answer, the executor's last report and how many rounds it took
@dataclass
class TeamResult:
    answer:str
    last_report:str | None
    rounds:int

# prints one block per message passed between the roles, pass it as step_tracer to watch the team work
def PrintTeamStep(role:str, model_name:str, text:str):
    print(f"\n--- {role} ({model_name}) ---\n{text}\n")

class PlannerExecutor():
    def __init__(self, planner:OpenAIModel, executor:OpenAIModel, max_rounds:int = 5,
                 final_marker:str = TeamPrompts.final_marker, step_tracer:Callable | None = None):
        self.planner = planner
        self.executor = executor
        self.max_rounds = max(max_rounds, 1) # planner -> executor exchanges allowed for one task
        self.final_marker = final_marker # the planner starts its reply with this when the task is done
        self.step_tracer = step_tracer # optional hook called with (role, model_name, text) for every message, see PrintTeamStep

    def Trace(self, role:str, model:OpenAIModel, text:str):
        if self.step_tracer:
            self.step_tracer(role, model.model_name, text)

    # the planner's reply is final when a line starts with the marker, everything after the marker is the answer.
    # models often write a sentence before it, so the marker does not have to be on the first line
    def FinalAnswer(self, planner_reply:str) -> str | None:
        marker = self.final_marker.upper()
        lines = planner_reply.splitlines()
        for index, line in enumerate(lines):
            stripped = line.strip().lstrip("*#> ") # markdown like **FINAL ANSWER:** is common
            if stripped.upper().startswith(marker):
                first = stripped[len(marker):].lstrip("* ") # the closing ** of a bold marker
                return "\n".join([first, *lines[index + 1:]]).strip()
        return None

    # runs one task through the team. if anything fails both conversations go back to how they were, and the error is raised
    def Run(self, task:str) -> TeamResult:
        planner_length = self.planner.context_manager.GetConversationLength()
        executor_length = self.executor.context_manager.GetConversationLength()

        try:
            return self.RunRounds(task)
        except Exception:
            self.planner.context_manager.TruncateConversation(planner_length)
            self.executor.context_manager.TruncateConversation(executor_length)
            raise

    def RunRounds(self, task:str) -> TeamResult:
        planner_input = TeamPrompts.task.format(task=task)
        last_report = None

        for round_number in range(1, self.max_rounds + 1):
            instructions = StripReasoning(self.planner.MultiShotMessage(planner_input))

            answer = self.FinalAnswer(instructions)
            if answer is not None:
                # a bare marker means "the executor's last report is the answer"
                return TeamResult(answer=answer or last_report or "", last_report=last_report, rounds=round_number)
            self.Trace(PLANNER, self.planner, instructions) # the final answer is returned, not traced, so it is shown once

            last_report = StripReasoning(self.executor.MultiShotMessage(instructions))
            self.Trace(EXECUTOR, self.executor, last_report)

            planner_input = TeamPrompts.report.format(report=last_report)

        raise MaxRoundsError(self.max_rounds, last_report)

    # ---- changing the models behind the roles ----
    def GetRole(self, role:str) -> OpenAIModel:
        if role == PLANNER:
            return self.planner
        if role == EXECUTOR:
            return self.executor
        raise HarnessBaseError(f"no role named {role}, use {PLANNER} or {EXECUTOR}")

    # gives a role another model, and optionally another provider. the role keeps its conversation, tools and prompt
    def SetRoleModel(self, role:str, model_name:str, provider=None):
        model = self.GetRole(role)
        provider = provider or model.provider
        if not provider.IsModelAvailable(model_name):
            raise HarnessBaseError(f"model {model_name} not available on {provider.name}, use one of {provider.available_models}")
        model.model_name = model_name
        model.provider = provider

    # the planner's model becomes the executor's and the other way round. tools, prompts and response settings stay with the role
    def SwapModels(self):
        planner_model, planner_provider = self.planner.model_name, self.planner.provider
        self.planner.model_name, self.planner.provider = self.executor.model_name, self.executor.provider
        self.executor.model_name, self.executor.provider = planner_model, planner_provider

    # clears both conversations
    def Reset(self):
        self.planner.ResetModelMessageQueue()
        self.executor.ResetModelMessageQueue()
