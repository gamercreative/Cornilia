from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

"""
    endpoints go here and are served thorugh this endpoint
"""

# this endpoint is to get the conversation_history based on hash
@app.get("/multi-shot/{conversation_hash}")
def get_conversation(conversation_hash):
    return "conversatino"

# this endpoint is to append to the conversation
@app.post("/multis-shot/")
def continue_conversation(conversation_hash,message):
    return "message etc appended history"

# this endpoint is to one shot messages without hisotry without memory
@app.post("/one-shot")
def create_conversation():
    return "the message only bare bone"
