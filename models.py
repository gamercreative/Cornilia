from providers import *
from system import *
from errors import *

class OpenAIModel():
    def __init__(self, model_name:str, provider:OpenAIProvider, context_manager , response_configs:dict):
        self.provider = provider
        self.model_name = model_name
        self.response_configs = response_configs
        self.context_manager = context_manager

        self.ResetModelMessageQueue() # init the message queue to be used later

    # used to create or reset the message queue for the model        
    def ResetModelMessageQueue(self): # this is a queue of model messages no matter the model for multi turn convos
        self.MessageHistory = []

    # used to parse response types from the models using the openai package response strcuture
    def HandleModelResponse(self, response) -> str | None:
        if not isinstance(response, ChatCompletion):
            raise HarnessBaseError("error the repsones from the openai model passed on is not ChatCompletion")

        response_finish_reason = response.choices[0].finish_reason

        # successful generation though not of content but hte model is content with what it has given
        if response_finish_reason == "stop":
            text_response = response.choices[0].message.content 
            self.context_manager.AppendAssistantMessage(response.choices[0].message.content)
            return str(text_response)
            
        # reutrn this if the model exceeds the max tokens limit set by the model object
        elif response_finish_reason == "length":
            self.context_manager.AppendSystemMessage(AgentErrorPrompts.length_exceeded)
            raise MaxTokenError(self.response_configs["max_tokens"])
        
    # a function to handle the multi shot messages sent to the model as to retain history etc
    def MutliShotMessage(self, prompt):
        self.context_manager.AppendUserMessage(prompt)
         
        response = self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.response_configs)
        
        try:
            res = self.HandleModelResponse(response)
            return res
        except Exception as e:
            print(f"{SystemResponseTypes.SystemError}: {e}")
            self.MutliShotMessage(prompt)
