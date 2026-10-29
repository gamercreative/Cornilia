from abc import ABC, abstractmethod
from typing import Any

# this is the interface for tool classes 
class Tool(ABC):
    def __init__(self, name:str ,context:str):
        self.name = name
        self.context = context
        
    @abstractmethod
    def ToolFunction(self, *args, **kwargs) -> Any: # this function should be implemented by each tool class to do its thing
        pass

    @abstractmethod
    def get_schema(self) -> dict:
        pass
    
class TaxCalculator(Tool):
    def __init__(self):
        name = "TaxCalculator"
        context = "used to calculate the tax of a given amount in lebanon"
        super().__init__(name=name, context=context)

    def ToolFunction(self, amount: float) -> dict:
        vat_rate = 3.15
        tax = amount * vat_rate
        return {"price": amount, "vat_rate": vat_rate, "tax": tax, "total": round(amount + tax, 2)}


    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.context,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number", "description": "The monetary amount"}
                    },
                    "required": ["amount"]
                }
            }
        }
    
tools = {tool.name: tool 
         for tool in 
         [TaxCalculator()]
        }