from abc import ABC, abstractmethod

# this is the interface for tool classes 
class Tool(ABC):
    def __init__(self, name:str ,context:str):
        self.name = name
        self.context = context
        
    @abstractmethod
    def ToolFunction(self, *args, **kwargs): # this function should be implemented by each tool class to do its thing
        pass
    
class TaxCalculator(Tool):
    def __init__(self):
        name = "TaxCalculator"
        context = "used to calculate the tax of a given amount in lebanon"
        super().__init__(name=name, context=context)

    def ToolFunction(self, amount: float):
        return amount * 3.15