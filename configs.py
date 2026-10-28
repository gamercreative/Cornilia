# these configs are used to connect the model. and their provider info
# PROVIDER_CONFIG = {"base_url": "http://localhost:11434/v1", "api_key": "ollama", "model": "qwen3.5:35b-a3b"}
# RESPONSE_CONFIG = {
#     "max_tokens": 128,
#     "reasoning_effort": "none"    
# }

# DEFINES THE DEVICE BEING USED
# DEVICE = "PRO"
DEVICE = "AIR"

class ProviderConfigs():
    def __init__(self, name:str, base_url:str, api_key:str, model_names:list[str]):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.model_names = model_names

"""
    Ollama provider configs
"""        
BASE_URL = "http://localhost:11434/v1"
API_KEY = "ollama"
MODEL_NAMES_PRO = ["qwen3.5:35b-a3b", "qwen3:8b" , "qwen2.5-coder:1.5b-base"]
MODEL_NAMES_AIR = ["qwen3.5:9b"]

if DEVICE == "AIR":
    OLLAMA_PROVIDER_CONFIGS = ProviderConfigs(name = "ollama", base_url = BASE_URL, api_key = API_KEY, model_names = MODEL_NAMES_AIR)
elif DEVICE == "PRO":
    OLLAMA_PROVIDER_CONFIGS = ProviderConfigs(name = "ollama", base_url = BASE_URL, api_key = API_KEY, model_names = MODEL_NAMES_PRO)

"""
    Model configs for qwen models im using
    these configs do not need to be able to save theere configs as strcutures because it differs between providers and such
"""
MODEL_RESPONSE_CONFIG = {
    "max_tokens": 2048,
    "reasoning_effort": "none"
}