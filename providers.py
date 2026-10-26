from openai import OpenAI
from openai.types.chat import ChatCompletion

# source code from this project
from configs import ProviderConfigs, OLLAMA_PROVIDER_CONFIGS ,MODEL_RESPONSE_CONFIG
from errors import *

# complimenting the Provider parent class
"""
    The provider is the way we speak to a model and the provider must show a name and a model name. to interface with the model
    the provider must also have its select list of models that we are using and such saved and ready to connect and message
    providers launch with their model_names already present we just select to interact with that model
"""
class OpenAIProvider():
    def __init__(self, provider_configs:ProviderConfigs):
        self.name     = provider_configs.name
        self.base_url = provider_configs.base_url
        self.api_key  = provider_configs.api_key
        self.available_models = provider_configs.model_names # this is a list of strings that are the names of models
        
        self.InitiateConnection() # this will connect us to the provider

    # initiate a client connection with the provider
    def InitiateConnection(self):
        self.client:OpenAI = OpenAI(
            base_url = self.base_url,
            api_key = self.api_key
        )

    # used to contact the model with a specific message in a 1 time thing given 1 prompt
    def OneShotMessage(self, prompt:dict, model_name:str, response_configs:dict) -> ChatCompletion:
        if model_name not in self.available_models:
            raise HarnessBaseError("model not available")
        
        response = self.client.chat.completions.create(
        model = model_name,
        messages = prompt,
        **response_configs
        )

        return response # is sucessful the function will return the chatcomletion type variable from create

# compliments the ModelClass parent class
