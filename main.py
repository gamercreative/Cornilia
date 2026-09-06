# imports used for project
from openai import OpenAI
import configs
import tools

class Provider():
    def __init__(self, name, base_url, api_key):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.InitiateConnection()
        
    def InitiateConnection(self):
        self.client:OpenAI = OpenAI(
            base_url = self.base_url,
            api_key = self.api_key
        )

class Model():
    def __init__(self, provider:Provider, model_name:str , response_configs:dict):
        self.provider = provider
        self.model_name = model_name
        self.response_configs = response_configs
        self.ResetModelMessageQueue()

    # used to create or reset the message queue for the model        
    def ResetModelMessageQueue(self): # this is a queue of model messages no matter the model for multi turn convos
        self.MessageHistory = []
        
    """
        This seciton later is to be connecetrd to a running db instance
    """
    def AppendToConversationHistory(self, message:dict):
        self.MessageHistory.append(message)
        
    def GetConversationHistory(self):
        return self.MessageHistory
    # the replace with db part ends here
      
    # usecd to ask one question to hte model with no history no nothing
    def OneShotMessage(self, prompt):
        response = self.provider.client.chat.completions.create(
        model = self.model_name,
        messages = prompt,
        **self.response_configs
        )
        
        return response
        
    def MutliShotMessage(self, prompt): # this is a queue of model messages no matter the model for multi turn
        UserMessageExchange = {"role": "user", "content": prompt}
        self.AppendToConversationHistory(UserMessageExchange)
        
        response = self.OneShotMessage(self.GetConversationHistory())
        text_response_from_result = response.choices[0].message.content
        
        ModelMessageExchange = {"role": "assistant", "content": text_response_from_result}
        self.AppendToConversationHistory(ModelMessageExchange)
        
        return text_response_from_result

# the loop for contacting the model and handlign the model inputs
provider = Provider("ollama", base_url= configs.BASE_URL, api_key=configs.API_KEY)
model = Model(provider, configs.MODEL_NAME, configs.RESPONSE_CONFIG)

while True:
    response = model.MutliShotMessage(input("enter message here: "))
    print(response)