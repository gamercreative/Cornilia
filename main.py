from system import *
from context_managers import *
from providers import *
from models import *

# the loop for contacting the model and handlign the model inputs

# connect to the provider
provider = OpenAIProvider(OLLAMA_PROVIDER_CONFIGS)

# initate the context manager
cache = CacheModelContextManager()

# initiate the model with the provider and context manager
model = OpenAIModel("qwen3.5:35b-a3b", provider=provider, context_manager=cache, response_configs=MODEL_RESPONSE_CONFIG)

while True:
    response = model.MutliShotMessage(input("enter message here: "))
    print(response)