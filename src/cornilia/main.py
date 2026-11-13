from .system import *
from .context_managers import *
from .providers import *
from .models import *
from .tools import *
from .orchestration import *
from . import settings
import openai

# reads the system prompt template from settings, SYSTEM_PROMPT first and SYSTEM_PROMPT_FILE if that is not set
def LoadSystemPrompt() -> str | None:
    prompt = settings.SYSTEM_PROMPT
    prompt_file = settings.ProjectPath(settings.SYSTEM_PROMPT_FILE)
    if not prompt and prompt_file:
        if not prompt_file.is_file():
            raise HarnessBaseError(f"system prompt file {prompt_file} not found, check CORNILIA_SYSTEM_PROMPT_FILE or SYSTEM_PROMPT_FILE")
        prompt = prompt_file.read_text(encoding="utf-8")
    return prompt

# connect to the provider from settings
def CreateProvider() -> OpenAIProvider:
    return OpenAIProvider(settings.PROVIDER_CONFIGS)

# initiate a model with its own context manager, so every model is its own conversation
# system_prompt replaces the configured one. either way it is a template: the context manager fills in runtime values
# like today's date each time the conversation starts or is reset
# response_configs replaces MODEL_RESPONSE_CONFIG
def CreateModel(provider:OpenAIProvider, tools:dict, model_name:str | None = None, system_prompt:str | None = None,
                response_configs:dict | None = None) -> OpenAIModel:
    model_name = model_name or settings.MODEL_NAME
    if not model_name:
        raise HarnessBaseError("no model set, set CORNILIA_MODEL or MODEL_NAME in configs.py")

    system_prompt = system_prompt or LoadSystemPrompt()

    return OpenAIModel(
        model_name,
        provider=provider,
        context_manager=CacheModelContextManager(system_prompt),
        tools=tools,
        response_configs=settings.MODEL_RESPONSE_CONFIG if response_configs is None else response_configs,
        max_steps=settings.MAX_STEPS,
        max_retries=settings.MAX_RETRIES,
        retry_max_wait=settings.RETRY_MAX_WAIT,
        tool_tracer=PrintToolTrace if settings.TRACE_TOOL_CALLS else None,
    )

# load the tools from the configured folders, a tool name found in two folders is an error
def CreateTools() -> dict:
    folders = settings.ToolsFolders(settings.TOOLS_DIR)
    return LoadTools(folders) if folders else {}

# the planner/executor team from settings. the planner gets no tools, it only thinks and gives instructions,
# the executor gets the tools. each role has its own conversation, and can have its own provider
def CreatePlannerExecutor(provider:OpenAIProvider, tools:dict, planner_model:str | None = None,
                          executor_model:str | None = None) -> PlannerExecutor:
    planner_model = planner_model or settings.PLANNER_MODEL
    if not planner_model:
        raise HarnessBaseError("no planner model set, set CORNILIA_PLANNER_MODEL or PLANNER_MODEL in configs.py")

    planner_provider = OpenAIProvider(settings.PLANNER_PROVIDER_CONFIGS) if settings.PLANNER_PROVIDER_CONFIGS else provider
    executor_provider = OpenAIProvider(settings.EXECUTOR_PROVIDER_CONFIGS) if settings.EXECUTOR_PROVIDER_CONFIGS else provider

    planner = CreateModel(planner_provider, {}, model_name=planner_model,
                          system_prompt=settings.PLANNER_SYSTEM_PROMPT or TeamPrompts.planner,
                          response_configs=settings.PLANNER_RESPONSE_CONFIG)
    executor = CreateModel(executor_provider, tools, model_name=executor_model or settings.EXECUTOR_MODEL,
                           system_prompt=settings.EXECUTOR_SYSTEM_PROMPT or TeamPrompts.executor,
                           response_configs=settings.EXECUTOR_RESPONSE_CONFIG)

    return PlannerExecutor(planner, executor, max_rounds=settings.MAX_ROUNDS, step_tracer=PrintTeamStep)

TEAM_COMMANDS = """commands: /planner <model> and /executor <model> change a role's model, /swap exchanges the two models,
/roles shows who does what, /reset clears both conversations"""

# handles a /command in team chat, returns False when the line is a task and not a command
def RunTeamCommand(team:PlannerExecutor, line:str) -> bool:
    if not line.startswith("/"):
        return False
    command, _, argument = line[1:].strip().partition(" ")
    argument = argument.strip()

    if command in (PLANNER, EXECUTOR) and argument:
        team.SetRoleModel(command, argument)
    elif command == "swap":
        team.SwapModels()
    elif command == "reset":
        team.Reset()
        print(f"{SystemResponseTypes.SystemInfo}: both conversations cleared")
        return True
    elif command != "roles":
        print(TEAM_COMMANDS)
        return True
    print(f"{SystemResponseTypes.SystemInfo}: planner {team.planner.model_name} on {team.planner.provider.name}, "
          f"executor {team.executor.model_name} on {team.executor.provider.name}")
    return True

# the terminal chat for a planner/executor team, every message is a task for the team
def RunTeamChat(team:PlannerExecutor):
    print(f"{SystemResponseTypes.SystemInfo}: planner {team.planner.model_name}, executor {team.executor.model_name}, "
          f"tools: {list(team.executor.tools) or 'none'}")
    print(TEAM_COMMANDS)

    while True:
        try:
            line = input("enter task here: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue

        try:
            if RunTeamCommand(team, line):
                continue
            result = team.Run(line)
            print(f"=== answer after {result.rounds} round(s) ===\n{result.answer}")
        except (HarnessBaseError, openai.OpenAIError) as e: # the task is undone, so the next one starts clean
            print(f"{SystemResponseTypes.SystemError}: {e}")

# the terminal chat loop, started with `python -m cornilia` or the `cornilia` command
def RunChat():
    # a setup problem (no model, missing prompt file, no api key) ends with one clear line instead of a traceback
    try:
        if settings.PLANNER_MODEL: # a planner turns the chat into a planner/executor team
            team = CreatePlannerExecutor(CreateProvider(), CreateTools())
        else:
            model = CreateModel(CreateProvider(), CreateTools())
    except (HarnessBaseError, openai.OpenAIError) as e:
        raise SystemExit(f"{SystemResponseTypes.SystemError}: {e}")

    if settings.PLANNER_MODEL:
        return RunTeamChat(team)

    print(f"{SystemResponseTypes.SystemInfo}: chatting with {model.model_name} on {model.provider.name}, tools: {list(model.tools) or 'none'}")

    while True:
        try:
            prompt = input("enter message here: ")
        except (EOFError, KeyboardInterrupt):
            break

        try:
            print(model.MultiShotMessage(prompt))
        except (HarnessBaseError, openai.OpenAIError) as e: # the turn is undone, so the next message starts clean
            print(f"{SystemResponseTypes.SystemError}: {e}")

if __name__ == "__main__":
    RunChat()
