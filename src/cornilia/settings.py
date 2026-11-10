import os
import sys
from pathlib import Path

from .providers import ProviderConfigs

"""
    Every setting Cornilia uses, with its default.
    Defaults come from environment variables, then an optional local configs.py (not committed, see configs.example.py)
    can override any name defined here. Values in configs.py win over environment variables.
"""

# presets for OpenAI-compatible providers, pick one with CORNILIA_PROVIDER
PROVIDER_PRESETS = {
    # a local server such as ollama, no real key needed
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3.5:9b",
        "response_configs": {"max_tokens": 512, "reasoning_effort": "none"},
    },
    # a hosted OpenAI-compatible api, the key is read from OPENAI_API_KEY and the model from CORNILIA_MODEL
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "model": None,
        "response_configs": {},
    },
}

# a relative path is taken from the folder you run from, which is your project when Cornilia is installed as a package
def ProjectPath(path:str | None) -> Path | None:
    if not path:
        return None
    return Path(path).expanduser()

# an integer setting from the environment, empty means the default
def EnvInt(name:str, default:int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}")

def EnvFlag(name:str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

PROVIDER_NAME = os.environ.get("CORNILIA_PROVIDER", "ollama")
if PROVIDER_NAME not in PROVIDER_PRESETS:
    raise ValueError(f"CORNILIA_PROVIDER={PROVIDER_NAME} is not one of {list(PROVIDER_PRESETS)}")
_preset = PROVIDER_PRESETS[PROVIDER_NAME]

# provider connection, CORNILIA_BASE_URL and CORNILIA_API_KEY override the preset for any other OpenAI-compatible server
PROVIDER_CONFIGS = ProviderConfigs(
    name = PROVIDER_NAME,
    base_url = os.environ.get("CORNILIA_BASE_URL", _preset["base_url"]),
    api_key = os.environ.get("CORNILIA_API_KEY", _preset["api_key"]),
)

# the model to talk to
MODEL_NAME = os.environ.get("CORNILIA_MODEL", _preset["model"])

# extra arguments for every chat completion request, they differ between providers
MODEL_RESPONSE_CONFIG = dict(_preset["response_configs"])
if os.environ.get("CORNILIA_MAX_TOKENS", "").strip(): # sent as max_tokens, the common OpenAI-compatible name
    MODEL_RESPONSE_CONFIG["max_tokens"] = EnvInt("CORNILIA_MAX_TOKENS", 0)

# system prompt for every new conversation, supports $date, $time, $datetime and $weekday
# set SYSTEM_PROMPT directly, or point CORNILIA_SYSTEM_PROMPT_FILE at a text file
SYSTEM_PROMPT = os.environ.get("CORNILIA_SYSTEM_PROMPT")
SYSTEM_PROMPT_FILE = os.environ.get("CORNILIA_SYSTEM_PROMPT_FILE")

# folder of tool modules to load, every Tool defined at the top of a .py file in it is registered
# none by default, try the examples with CORNILIA_TOOLS_DIR=examples/tools
TOOLS_DIR = os.environ.get("CORNILIA_TOOLS_DIR")

# agent loop limits
MAX_STEPS = EnvInt("CORNILIA_MAX_STEPS", 10)
MAX_RETRIES = EnvInt("CORNILIA_MAX_RETRIES", 1)
# the longest wait in seconds before a retry, whether it comes from the provider's Retry-After header or from backoff
RETRY_MAX_WAIT = EnvInt("CORNILIA_RETRY_MAX_WAIT", 30)

# print every tool call with its arguments, result and time taken
TRACE_TOOL_CALLS = EnvFlag("CORNILIA_TRACE")

# --- planner/executor team, see orchestration.py ---
# setting PLANNER_MODEL turns the terminal chat into a team: the planner model thinks and gives instructions,
# the executor model carries them out with the tools. leave it unset for the usual single model chat
PLANNER_MODEL = os.environ.get("CORNILIA_PLANNER_MODEL")
EXECUTOR_MODEL = os.environ.get("CORNILIA_EXECUTOR_MODEL") # none means MODEL_NAME

# a role can use another provider than PROVIDER_CONFIGS, for example a hosted planner and a local executor.
# set these to a ProviderConfigs in configs.py, none means PROVIDER_CONFIGS
PLANNER_PROVIDER_CONFIGS = None
EXECUTOR_PROVIDER_CONFIGS = None

# request options per role, none means MODEL_RESPONSE_CONFIG. the planner gets its own because reasoning
# models need room to think, and the ollama preset turns thinking off for the single model chat
PLANNER_RESPONSE_CONFIG = None
if os.environ.get("CORNILIA_PLANNER_MAX_TOKENS", "").strip() or PROVIDER_NAME == "ollama":
    PLANNER_RESPONSE_CONFIG = {"max_tokens": EnvInt("CORNILIA_PLANNER_MAX_TOKENS", 4096)}
EXECUTOR_RESPONSE_CONFIG = None

# system prompts per role, none means the built in prompts in system.py (TeamPrompts)
PLANNER_SYSTEM_PROMPT = os.environ.get("CORNILIA_PLANNER_SYSTEM_PROMPT")
EXECUTOR_SYSTEM_PROMPT = os.environ.get("CORNILIA_EXECUTOR_SYSTEM_PROMPT")

# planner -> executor exchanges allowed for one task
MAX_ROUNDS = EnvInt("CORNILIA_MAX_ROUNDS", 5)

# your project is the folder you run from. it goes on the import path so its configs.py, and any module your
# tools import, are found however Cornilia was started (python -m cornilia, the cornilia command, uvicorn or a script)
if os.getcwd() not in sys.path:
    sys.path.append(os.getcwd())

# local overrides, a missing configs.py is fine but an error inside it is not hidden
try:
    from configs import *
    # old configs.py files used other names, say so instead of ignoring them
    if "OLLAMA_PROVIDER_CONFIGS" in globals() and "PROVIDER_CONFIGS" not in vars(__import__("configs")):
        print("System_Warning: configs.py sets OLLAMA_PROVIDER_CONFIGS, rename it to PROVIDER_CONFIGS and set MODEL_NAME, see configs.example.py")
except ModuleNotFoundError as e:
    if e.name != "configs":
        raise
