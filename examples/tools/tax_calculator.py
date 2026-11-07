from tools import Tool

# an example of a tool written as a Tool subclass with a hand-written schema, for full control

class TaxCalculator(Tool):
    def __init__(self):
        name = "TaxCalculator"
        context = "used to calculate the tax of a given amount in lebanon"
        super().__init__(name=name, context=context)

    def ToolFunction(self, amount: float) -> dict:
        vat_rate = 0.11
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

tax_calculator = TaxCalculator()
