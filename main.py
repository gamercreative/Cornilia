# imports used for project
from openai import OpenAI
from configs import ProviderConfigs, OLLAMA_PROVIDER_CONFIGS ,MODEL_RESPONSE_CONFIG

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
    def OneShotMessage(self, prompt:dict, model_name:str, response_configs:dict):
        if model_name not in self.available_models:
            return "model not available"
        
        response = self.client.chat.completions.create(
        model = model_name,
        messages = prompt,
        **response_configs
        )

        return response

"""
    Uses the apps cache to handle model memory and if not persisted then its gone
"""
class CacheModelContextManager():
    def __init__(self):
        self.MessageHistory:list[dict] = []

    # apppends conversaton to exsiting convo in cache
    def AppendToConversationHistory(self, message:dict):
        self.MessageHistory.append(message)

    # get the history till the last part
    def GetConversationHistory(self):
        return self.MessageHistory

class Model():
    def __init__(self, model_name:str, provider:OpenAIProvider, context_manager , response_configs:dict):
        self.provider = provider
        self.model_name = model_name
        self.response_configs = response_configs
        self.context_manager = context_manager

        self.ResetModelMessageQueue() # init the message queue to be used later

    # used to create or reset the message queue for the model        
    def ResetModelMessageQueue(self): # this is a queue of model messages no matter the model for multi turn convos
        self.MessageHistory = []

    # a function to handle the multi shot messages sent to the model as to retain history etc
    def MutliShotMessage(self, prompt): # this is a queue of model messages no matter the model for multi turn
        UserMessageExchange = {"role": "user", "content": prompt}
        self.context_manager.AppendToConversationHistory(UserMessageExchange)
        
        response = self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.response_configs)
        if response is str:
            return response

        text_response_from_result = response.choices[0].message.content
        
        ModelMessageExchange = {"role": "assistant", "content": text_response_from_result}
        self.context_manager.AppendToConversationHistory(ModelMessageExchange)
        
        return text_response_from_result

# the loop for contacting the model and handlign the model inputs

provider = OpenAIProvider(OLLAMA_PROVIDER_CONFIGS)
cache = CacheModelContextManager()
model = Model("qwen3.5:35b-a3b", provider=provider, context_manager=cache, response_configs=MODEL_RESPONSE_CONFIG)

while True:
    response = model.MutliShotMessage(input("enter message here: "))
    print(response)