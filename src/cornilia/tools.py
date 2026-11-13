import importlib.util
import inspect
import json
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

    # a TypedDict describes a nested object, so the model sees its field names, types and which ones are required
    if typing.is_typeddict(hint):
        fields = typing.get_type_hints(hint, include_extras=True)
        required = [name for name in fields if name in hint.__required_keys__]
        schema = {"type": "object", "properties": {name: TypeToSchema(field) for name, field in fields.items()}}
        if required:
            schema["required"] = required
        if hint.__doc__ and not hint.__doc__.startswith(hint.__name__ + "("): # skip the docstring python writes itself
            schema["description"] = inspect.cleandoc(hint.__doc__)
        return schema

    if hint in JSON_TYPES:
        return {"type": JSON_TYPES[hint]}

    return {} # unknown types accept anything

# the type a hint stands for once Annotated and Optional are taken off, and whether it allows None
def BaseType(hint) -> tuple[Any, bool]:
    if typing.get_origin(hint) is Annotated:
        hint = typing.get_args(hint)[0]
    if typing.get_origin(hint) in (Union, types.UnionType):
        options = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        allows_none = len(options) < len(typing.get_args(hint))
        return (options[0] if len(options) == 1 else hint), allows_none
    return hint, hint is Any

# smaller models often send "10" for a number, "true" for a flag or a list written as a json string.
# when the type hint makes the meaning clear the value is converted, anything else is passed on as it came
def CoerceValue(value, hint):
    base, _ = BaseType(hint)
    origin = typing.get_origin(base) or base
    if not isinstance(value, str):
        if base is float and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        return value
    text = value.strip()
    try:
        if base is bool and text.lower() in ("true", "false"):
            return text.lower() == "true"
        if base is int:
            return int(text)
        if base is float:
            return float(text)
        if origin in (list, tuple, set) and text.startswith("["):
            return json.loads(text)
        if (origin is dict or typing.is_typeddict(base)) and text.startswith("{"):
            return json.loads(text)
    except ValueError: # not what the hint asks for, the tool's own error will tell the model
        pass
    return value

# a tool built from a plain function: its name, docstring and type hints become the schema
class FunctionTool(Tool):
    def __init__(self, function:Callable, name:str | None = None, context:str | None = None):
        self.function = function
        docstring = inspect.getdoc(function) or ""
        super().__init__(name=name or function.__name__, context=context or docstring.split("\n\n")[0].strip())

    def ToolFunction(self, *args, **kwargs) -> Any:
        return self.function(*args, **self.PrepareArguments(kwargs))

    # makes the model's arguments fit the function: values are converted to the hinted type where that is clear,
    # and a null sent for an optional argument whose default is not None means "use the default"
    def PrepareArguments(self, arguments:dict) -> dict:
        hints = typing.get_type_hints(self.function, include_extras=True)
        parameters = inspect.signature(self.function).parameters
        prepared = {}
        for name, value in arguments.items():
            parameter = parameters.get(name)
            if parameter is None: # unknown names are passed on, so the error tells the model what it got wrong
                prepared[name] = value
                continue
            hint = hints.get(name, Any)
            if value is None and parameter.default is not parameter.empty and not BaseType(hint)[1]:
                continue
            prepared[name] = CoerceValue(value, hint)
        return prepared

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
