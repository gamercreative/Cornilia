# local configuration template
# copy this file to configs.py (it is not committed) and change only what you need.
# anything you leave out keeps the default from settings.py, which also reads environment variables.
# keep secrets out of this template, put real keys in your configs.py or in environment variables.

# import os
# from cornilia.providers import ProviderConfigs

# --- provider: any OpenAI-compatible server ---
# PROVIDER_CONFIGS = ProviderConfigs(
#     name = "ollama",
#     base_url = "http://localhost:11434/v1",
#     api_key = "ollama",
#     model_names = ["qwen3.5:9b", "qwen3:8b"], # optional allow list, leave out to allow any model
# )

# a hosted api instead, set the model too and clear the ollama-only response options
# PROVIDER_CONFIGS = ProviderConfigs(name = "openai", base_url = "https://api.openai.com/v1", api_key = os.environ["OPENAI_API_KEY"])
# MODEL_NAME = "<model name>"
# MODEL_RESPONSE_CONFIG = {} # reasoning models take {"max_completion_tokens": ...} instead of max_tokens

# --- model ---
# MODEL_NAME = "qwen3.5:9b"
# MODEL_RESPONSE_CONFIG = {"max_tokens": 512, "reasoning_effort": "none"}

# --- system prompt, $date $time $datetime and $weekday are filled in when a conversation starts ---
# SYSTEM_PROMPT = "You are a helpful assistant. Today is $weekday $date."
# SYSTEM_PROMPT_FILE = "prompts/system.txt" # used when SYSTEM_PROMPT is not set

# --- tools and the agent loop ---
# TOOLS_DIR = "tools" # none by default. a relative path is taken from the folder you run from
# MAX_STEPS = 10
# MAX_RETRIES = 1
# TRACE_TOOL_CALLS = True
