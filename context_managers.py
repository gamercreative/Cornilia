from system import *

# Compliments the ModelContextManager parent class
"""
    Uses the apps cache to handle model memory and if not persisted then its gone
"""
class CacheModelContextManager():
    def __init__(self):
        self.MessageHistory:list[dict] = []

    # apppends conversaton to exsiting convo in cache
    def AppendToConversation(self, message:dict):
        self.MessageHistory.append(message)
        
    # appends a models output to history
    def AppendAssistantMessage(self, message:str):
        self.AppendToConversation({"role": Role.ASSISTANT, "content" : message})
    
    # appends a users prompt to history
    def AppendUserMessage(self, message:str):
        self.AppendToConversation({"role": Role.USER, "content" : message})
        
    # appends a system message to history
    def AppendSystemMessage(self, message:str):
        self.AppendToConversation({"role": Role.SYSTEM, "content" : message})

    # get the history till the last part
    def GetConversationHistory(self) -> list[dict]:
        return self.MessageHistory
    
    def AppendToolCallMessage(self, message):
        self.AppendToConversation(message.model_dump(exclude_none=True))

    # appends a tools result, tied to the call that asked for it
    def AppendToolResult(self, tool_call_id:str, result:str):
        self.AppendToConversation({"role": Role.TOOL, "tool_call_id": tool_call_id, "content": result})
