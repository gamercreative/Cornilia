from .system import *
from .context_managers import *
from .providers import *
from .models import *
from .tools import *
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
def CreateModel(provider:OpenAIProvider, tools:dict, model_name:str | None = None, system_prompt:str | None = None) -> OpenAIModel:
    model_name = model_name or settings.MODEL_NAME
    if not model_name:
        raise HarnessBaseError("no model set, set CORNILIA_MODEL or MODEL_NAME in configs.py")

    system_prompt = system_prompt or LoadSystemPrompt()

    return OpenAIModel(
        model_name,
        provider=provider,
        context_manager=CacheModelContextManager(system_prompt),
        tools=tools,
        response_configs=settings.MODEL_RESPONSE_CONFIG,
        max_steps=settings.MAX_STEPS,
        max_retries=settings.MAX_RETRIES,
        retry_max_wait=settings.RETRY_MAX_WAIT,
        tool_tracer=PrintToolTrace if settings.TRACE_TOOL_CALLS else None,
    )

# load the tools from the configured folder
def CreateTools() -> dict:
    tools_dir = settings.ProjectPath(settings.TOOLS_DIR)
    return LoadTools(tools_dir) if tools_dir else {}

# the terminal chat loop, started with `python -m cornilia` or the `cornilia` command
def RunChat():
    # a setup problem (no model, missing prompt file, no api key) ends with one clear line instead of a traceback
    try:
        model = CreateModel(CreateProvider(), CreateTools())
    except (HarnessBaseError, openai.OpenAIError) as e:
        raise SystemExit(f"{SystemResponseTypes.SystemError}: {e}")
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
