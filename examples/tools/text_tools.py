from typing import Annotated

from tools import tool

# an example of a tool written as a plain function, the schema comes from the docstring and type hints
@tool
def count_words(text: Annotated[str, "The text to count the words of"]) -> dict:
    """Counts the words and characters in a piece of text."""
    return {"words": len(text.split()), "characters": len(text)}
