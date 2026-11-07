import importlib.util
import inspect
import sys
import types
import typing
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

from .system import *

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

# python types and the json schema type the model sees for them
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}

# turns one type hint into a json schema, Annotated[float, "text"] adds a description
def TypeToSchema(hint) -> dict:
    origin = typing.get_origin(hint)

    if origin is Annotated:
        base, *extras = typing.get_args(hint)
        schema = TypeToSchema(base)
        descriptions = [extra for extra in extras if isinstance(extra, str)]
        if descriptions:
            schema["description"] = descriptions[0]
        return schema

    if origin is Literal:
        values = list(typing.get_args(hint))
        return {"type": JSON_TYPES.get(type(values[0]), "string"), "enum": values}

    if origin in (Union, types.UnionType): # Optional[x] and x | None become the type of x
        options = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        return TypeToSchema(options[0]) if len(options) == 1 else {}

    if origin in (list, tuple, set) or hint is list: # openai rejects an array schema without items
        args = typing.get_args(hint)
        return {"type": "array", "items": TypeToSchema(args[0]) if args else {}}

    if origin is dict:
        return {"type": "object"}

    if hint in JSON_TYPES:
        return {"type": JSON_TYPES[hint]}

    return {} # unknown types accept anything

# a tool built from a plain function: its name, docstring and type hints become the schema
class FunctionTool(Tool):
    def __init__(self, function:Callable, name:str | None = None, context:str | None = None):
        self.function = function
        docstring = inspect.getdoc(function) or ""
        super().__init__(name=name or function.__name__, context=context or docstring.split("\n\n")[0].strip())

    def ToolFunction(self, *args, **kwargs) -> Any:
        return self.function(*args, **kwargs)

    # calling the tool directly runs the original function, handy for testing it
    def __call__(self, *args, **kwargs) -> Any:
        return self.function(*args, **kwargs)

    def get_schema(self) -> dict:
        hints = typing.get_type_hints(self.function, include_extras=True)
        properties = {}
        required = []

        for parameter in inspect.signature(self.function).parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            properties[parameter.name] = TypeToSchema(hints.get(parameter.name, Any))
            if parameter.default is parameter.empty:
                required.append(parameter.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.context,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

# decorator that turns a function into a tool, use as @tool, @tool(name="other_name") or tool(function, name="other_name")
def tool(function:Callable | None = None, *, name:str | None = None, context:str | None = None):
    if function is None:
        return lambda f: FunctionTool(f, name=name, context=context)
    return FunctionTool(function, name=name, context=context)

# builds the name -> tool registry, a duplicate name is skipped with a warning
def BuildToolRegistry(tool_list:list[Tool]) -> dict[str, Tool]:
    registry = {}
    for item in tool_list:
        if item.name in registry:
            print(f"{SystemResponseTypes.SystemWarning}: duplicate tool name {item.name}, skipping the second one")
            continue
        try:
            item.get_schema() # a tool whose schema can't be built is skipped like a broken import
        except Exception as e:
            print(f"{SystemResponseTypes.SystemWarning}: could not build the schema of tool {item.name}, skipping it: {e}")
            continue
        registry[item.name] = item
    return registry

# imports every .py file in a folder and collects the Tool objects defined at the top of each module
# a module that fails to import is skipped with a warning instead of crashing the agent
def LoadTools(folder:str | Path) -> dict[str, Tool]:
    folder = Path(folder)
    if not folder.is_dir():
        print(f"{SystemResponseTypes.SystemWarning}: tools folder {folder} does not exist, running without tools")
        return {}

    found = []
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"cornilia_tools_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec) # type: ignore
            sys.modules[module_name] = module # dataclasses and typing look the module up by name
            spec.loader.exec_module(module) # type: ignore
        except Exception as e:
            sys.modules.pop(module_name, None) # drop the half loaded module
            print(f"{SystemResponseTypes.SystemWarning}: could not load tools from {path.name}: {e}")
            continue
        module_tools = [value for value in vars(module).values() if isinstance(value, Tool)]
        # a function without @tool is a plain function, so say so instead of skipping the file silently
        if not module_tools:
            print(f"{SystemResponseTypes.SystemWarning}: {path.name} defines no tools. Put @tool on the line above each tool function, "
                  f"or start the file name with _ if it only holds helpers")
            continue
        for value in module_tools:
            # the same tool imported into a second file is one tool, not a duplicate
            if all(value is not other for other in found):
                found.append(value)

    return BuildToolRegistry(found)
