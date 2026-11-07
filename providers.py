from dataclasses import dataclass

from openai import OpenAI
from openai.types.chat import ChatCompletion

# source code from this project
from errors import *

# the connection details for one OpenAI-compatible provider
@dataclass
class ProviderConfigs():
    name:str
    base_url:str
    api_key:str
    model_names:list[str] | None = None # optional allow list, None or empty means any model the provider serves
    timeout:float = 120 # seconds before a request to the provider gives up

# complimenting the Provider parent class
"""
    The provider is the way we speak to a model and the provider must show a name and a model name. to interface with the model
    the provider can also have a select list of models that we allow, if it has none then any model the provider serves is allowed
"""
class OpenAIProvider():
    def __init__(self, provider_configs:ProviderConfigs):
        self.name     = provider_configs.name
        self.base_url = provider_configs.base_url
        self.api_key  = provider_configs.api_key
        self.available_models = provider_configs.model_names or [] # this is a list of strings that are the names of models
        self.timeout  = getattr(provider_configs, "timeout", 120)

        self.InitiateConnection() # this will connect us to the provider

    # initiate a client connection with the provider
    def InitiateConnection(self):
        self.client:OpenAI = OpenAI(
            base_url = self.base_url,
            api_key = self.api_key,
            timeout = self.timeout,
            max_retries = 0 # retries are handled by OpenAIModel.CallModel, so the sdk does not add its own
        )

    # an empty allow list means every model is allowed
    def IsModelAvailable(self, model_name:str) -> bool:
        return not self.available_models or model_name in self.available_models

    # used to contact the model with a specific message in a 1 time thing given 1 prompt
    def OneShotMessage(self, prompt:list[dict], model_name:str, tools ,response_configs:dict) -> ChatCompletion:
        if not self.IsModelAvailable(model_name):
            raise HarnessBaseError(f"model {model_name} not available, use one of {self.available_models}")

        if tools:
            response_configs = {**response_configs, "tools": tools} # some servers reject an empty tools list

        response = self.client.chat.completions.create(
        model = model_name,
        messages = prompt,
        **response_configs
        )

        return response # is sucessful the function will return the chatcomletion type variable from create
