from system import *

# Compliments the ModelContextManager parent class
"""
    Uses the apps cache to handle model memory and if not persisted then its gone
"""
class CacheModelContextManager():
    def __init__(self, system_prompt:str | None = None):
        # a template, if given it is always the first message of the conversation. see RenderSystemPrompt for $date and friends
        self.system_prompt = system_prompt
        self.ResetConversation()

    # clears the history, keeping the system prompt as the first message
    # the prompt is rendered here, so a reset gets today's date and time rather than the ones from when the model was made
    def ResetConversation(self):
        self.MessageHistory:list[dict] = []
        if self.system_prompt:
            self.AppendSystemMessage(RenderSystemPrompt(self.system_prompt))

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

    # number of messages in the history, used to remember a point to roll back to
    def GetConversationLength(self) -> int:
        return len(self.MessageHistory)

    # drops every message after the first `length` messages, used to undo a turn that failed
    def TruncateConversation(self, length:int):
        del self.MessageHistory[length:]
    
    def AppendToolCallMessage(self, message):
        self.AppendToConversation(message.model_dump(exclude_none=True))

    # appends a tools result, tied to the call that asked for it
    def AppendToolResult(self, tool_call_id:str, result:str):
        self.AppendToConversation({"role": Role.TOOL, "tool_call_id": tool_call_id, "content": result})
