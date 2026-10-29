from providers import *
from system import *
from errors import *
import json

class OpenAIModel():
    def __init__(self, model_name:str, provider:OpenAIProvider, context_manager, tools: dict , response_configs:dict):
        self.provider = provider
        self.model_name = model_name
        self.response_configs = response_configs
        self.context_manager = context_manager
        self.tools = tools
        self.api_tools = [tool.get_schema() for tool in tools.values()]
    
        self.ResetModelMessageQueue() # init the message queue to be used later
        
    def RunTool(self,tool_call) -> str:
        try:
            tool_args = json.loads(tool_call.function.arguments)
            tool = self.tools[tool_call.function.name]
            return json.dumps(tool.ToolFunction(**tool_args))
        
        except Exception as e:
            return f"{SystemResponseTypes.SystemError}: tool {tool_call.function.name} failed: {e}"

    # used to create or reset the message queue for the model        
    def ResetModelMessageQueue(self): # this is a queue of model messages no matter the model for multi turn convos
        self.MessageHistory = []

    # used to parse response types from the models using the openai package response strcuture
    def HandleModelResponse(self, resp) -> str | None:
        if not isinstance(resp, ChatCompletion):
            raise HarnessBaseError("error the repsones from the openai model passed on is not ChatCompletion")

        response = resp.choices[0]
        response_finish_reason = response.finish_reason
        
        # successful generation though not of content but hte model is content with what it has given
        if response_finish_reason == "stop":
            text_response = response.message.content 
            self.context_manager.AppendAssistantMessage(text_response)
            return str(text_response)
        
        elif response_finish_reason == "tool_calls":
            self.context_manager.AppendToolCallMessage(response.message)
            
            for tool_call in response.message.tool_calls: # type: ignore
                result = self.RunTool(tool_call)
                self.context_manager.AppendToolResult(tool_call.id, result)
                
            next_resp = self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.api_tools, self.response_configs)
            return self.HandleModelResponse(next_resp)

        # reutrn this if the model exceeds the max tokens limit set by the model object
        elif response_finish_reason == "length":
            self.context_manager.AppendSystemMessage(AgentErrorPrompts.length_exceeded)
            next_resp = self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.api_tools, self.response_configs)
            
            return self.HandleModelResponse(next_resp)

        
    # a function to handle the multi shot messages sent to the model as to retain history etc
    def MutliShotMessage(self, prompt):
        self.context_manager.AppendUserMessage(prompt)
         
        response = self.provider.OneShotMessage(self.context_manager.GetConversationHistory(), self.model_name, self.api_tools, self.response_configs)
        
        try:
            res = self.HandleModelResponse(response)
            return res
        except Exception as e:
            print(f"{SystemResponseTypes.SystemError}: {e}")
            self.MutliShotMessage(prompt)
