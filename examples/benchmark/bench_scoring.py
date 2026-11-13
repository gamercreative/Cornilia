"""
    Deterministic scoring for the benchmark. Nothing here calls a model.

    Three things are checked for a task run:
      - every tool call is valid: the arguments parse as a JSON object, the tool exists, and the arguments fit its schema
      - the arguments are correct: the calls the task needs were made with the right values (see Call, OneOf, AllOf)
      - the final answer matches (see the answer checks at the end of this file)
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# --- tool call validity ---

# one tool call as the model sent it, before Cornilia ran it
@dataclass
class RecordedCall:
    name: str
    raw_arguments: object # a JSON string from most servers, an object from a few
    arguments: dict | None = None # the parsed arguments, None when they did not parse
    errors: list[str] = field(default_factory=list) # why the call is not valid, empty when it is

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"name": self.name, "arguments": self.arguments if self.arguments is not None else self.raw_arguments,
                "valid": self.valid, "errors": self.errors}

# checks one value against the JSON schema Cornilia builds from type hints, returns a list of problems
def SchemaErrors(schema: dict, value, path: str = "arguments", top_level: bool = False) -> list[str]:
    if not schema: # an empty schema accepts anything
        return []
    errors = []
    kind = schema.get("type")

    if "enum" in schema and value not in schema["enum"]:
        return [f"{path} is {value!r}, expected one of {schema['enum']}"]

    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path} should be an object, got {type(value).__name__}"]
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value or value[name] is None:
                errors.append(f"{path}.{name} is required")
        for name, item in value.items():
            if name not in properties:
                # an unknown top-level name breaks the python call, an extra key inside a nested object is passed along
                if top_level:
                    errors.append(f"{path} has an unknown argument {name!r}")
                continue
            if item is None and name not in schema.get("required", []):
                continue # null for an optional argument means "use the default" in Cornilia
            errors += SchemaErrors(properties[name], item, f"{path}.{name}")
    elif kind == "array":
        if not isinstance(value, list):
            return [f"{path} should be an array, got {type(value).__name__}"]
        for index, item in enumerate(value):
            errors += SchemaErrors(schema.get("items", {}), item, f"{path}[{index}]")
    elif kind == "string" and not isinstance(value, str):
        errors.append(f"{path} should be a string, got {type(value).__name__}")
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        errors.append(f"{path} should be an integer, got {type(value).__name__}")
    elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(f"{path} should be a number, got {type(value).__name__}")
    elif kind == "boolean" and not isinstance(value, bool):
        errors.append(f"{path} should be a boolean, got {type(value).__name__}")
    return errors

# parses and checks a tool call against the tools the model was given ({name: tool schema})
def CheckToolCall(name: str, raw_arguments, schemas: dict) -> RecordedCall:
    call = RecordedCall(name=name, raw_arguments=raw_arguments)
    if isinstance(raw_arguments, dict):
        call.arguments = raw_arguments
    else:
        try:
            parsed = json.loads(raw_arguments or "{}")
        except (TypeError, ValueError) as e:
            call.errors.append(f"arguments are not valid JSON: {e}")
            return call
        if not isinstance(parsed, dict):
            call.errors.append(f"arguments are not a JSON object: {parsed!r}")
            return call
        call.arguments = parsed

    if name not in schemas:
        call.errors.append(f"no tool named {name!r}")
        return call
    call.errors += SchemaErrors(schemas[name]["function"]["parameters"], call.arguments, top_level=True)
    return call

# --- argument correctness ---

class _Absent:
    def __repr__(self):
        return "ABSENT"

ABSENT = _Absent() # the argument must be left out or null, for example "no status filter"

# a string argument that must contain this text, case ignored
@dataclass(frozen=True)
class Like:
    text: str

def _Number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None

# whether an actual value matches the expected one. dicts match on the expected keys only, lists match in any order,
# strings ignore case and outer spaces, and numbers may come as text (Cornilia converts those before the tool runs)
def ValueMatches(expected, actual) -> bool:
    if isinstance(expected, Like):
        return isinstance(actual, str) and expected.text.casefold() in actual.casefold()
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            if value is ABSENT:
                if actual.get(key) is not None:
                    return False
            elif key not in actual or not ValueMatches(value, actual[key]):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for item in expected:
            match = next((index for index, other in enumerate(remaining) if ValueMatches(item, other)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    if isinstance(expected, bool):
        return actual is expected or (isinstance(actual, str) and actual.strip().lower() == str(expected).lower())
    if isinstance(expected, (int, float)):
        number = _Number(actual)
        return number is not None and abs(number - expected) < 1e-6
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().casefold() == expected.casefold()
    return expected == actual

# the model called this tool with at least these arguments, in some call of the run
class Call:
    def __init__(self, tool_name: str, /, **arguments): # positional only, so a tool argument can be called name
        self.name = tool_name
        self.arguments = arguments

    def matches(self, calls: list[RecordedCall]) -> bool:
        return any(call.name == self.name and call.arguments is not None and ValueMatches(self.arguments, call.arguments)
                   for call in calls)

    def describe(self) -> str:
        return f"{self.name}({', '.join(f'{key}={value!r}' for key, value in self.arguments.items())})"

# one of several ways of doing it is enough
class OneOf:
    def __init__(self, *options):
        self.options = options

    def matches(self, calls) -> bool:
        return any(option.matches(calls) for option in self.options)

    def describe(self) -> str:
        return " or ".join(f"[{option.describe()}]" for option in self.options)

# every one of these is needed
class AllOf:
    def __init__(self, *parts):
        self.parts = parts

    def matches(self, calls) -> bool:
        return all(part.matches(calls) for part in self.parts)

    def describe(self) -> str:
        return " and ".join(part.describe() for part in self.parts)

# the model made no tool call at all, for a question that needs none
class NoCalls:
    def matches(self, calls) -> bool:
        return not calls

    def describe(self) -> str:
        return "no tool call"

# a tool call written into the reply text instead of being sent as a call, "leaked" into the answer
def LeakedToolCall(text: str, tool_names) -> bool:
    text = text or ""
    if re.search(r"<tool_call>|<function=|\[TOOL_CALLS\]", text):
        return True
    names = "|".join(re.escape(name) for name in tool_names)
    return bool(names) and bool(re.search(rf'"name"\s*:\s*"(?:{names})"[^{{}}]*"(?:arguments|parameters)"', text))

# --- final answer checks, each returns (passed, note) ---

def CleanAnswer(text: str) -> str:
    text = THINK_BLOCK.sub("", text or "")
    if "<think>" in text.lower(): # cut off in the middle of thinking
        text = text[: text.lower().index("<think>")]
    return text.strip()

# the text after the last "ANSWER:", or the whole reply when there is none
def FinalValue(text: str) -> tuple[str, bool]:
    matches = list(re.finditer(r"answer\s*[:=]\s*(.+)", CleanAnswer(text), re.IGNORECASE))
    if matches:
        return matches[-1].group(1).strip().strip("*`").strip(), True
    return CleanAnswer(text), False

def _Fold(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().replace("**", "")).strip()

def _NumbersIn(text: str) -> list[float]:
    return [float(number.replace(",", "")) for number in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text)]

# the reply mentions every one of these and none of the others
class Mentions:
    def __init__(self, *needed: str, forbidden: tuple = (), final_only: bool = False):
        self.needed = needed
        self.forbidden = forbidden
        self.final_only = final_only # look only at the ANSWER: line when there is one

    def __call__(self, text: str) -> tuple[bool, str]:
        value = FinalValue(text)[0] if self.final_only else CleanAnswer(text)
        folded = _Fold(value)
        missing = [item for item in self.needed if item.casefold() not in folded]
        present = [item for item in self.forbidden if re.search(rf"(?<![\w-]){re.escape(item.casefold())}(?![\w-])", folded)]
        if missing:
            return False, f"missing {missing}"
        if present:
            return False, f"also mentions {present}"
        return True, ""

    def describe(self) -> str:
        return f"mentions {list(self.needed)}" + (f", not {list(self.forbidden)}" if self.forbidden else "")

# the reply gives this number: the first number on the ANSWER: line, or else any number in the reply
class NumberIs:
    def __init__(self, expected: float, tolerance: float = 0.011):
        self.expected = expected
        self.tolerance = tolerance

    def __call__(self, text: str) -> tuple[bool, str]:
        value, has_line = FinalValue(text)
        numbers = _NumbersIn(value)
        candidates = numbers[:1] if has_line else numbers
        if any(abs(number - self.expected) <= self.tolerance for number in candidates):
            return True, ""
        return False, f"expected {self.expected:g}, found {candidates[:5] or 'no number'}"

    def describe(self) -> str:
        return f"number {self.expected:g}"

# a count given next to its label, like "6 open" or "open: 6"
class CountsAre:
    def __init__(self, **counts: int):
        self.counts = counts

    def __call__(self, text: str) -> tuple[bool, str]:
        folded = _Fold(CleanAnswer(text))
        wrong = []
        for label, count in self.counts.items():
            near = rf"{label}\W{{0,3}}(?:issues?\W{{0,3}})?(?:[a-z]+\W{{1,3}}){{0,2}}{count}\b|\b{count}\W{{0,3}}(?:[a-z]+\W{{1,3}}){{0,1}}{label}"
            if not re.search(near, folded):
                wrong.append(f"{label}={count}")
        return (not wrong), (f"no {wrong} in the reply" if wrong else "")

    def describe(self) -> str:
        return ", ".join(f"{label}={count}" for label, count in self.counts.items())

# the reply is JSON that holds these values. strict means the whole reply must parse as it is,
# otherwise a ```json fence around it is allowed (and noted)
class JsonMatches:
    def __init__(self, expected: dict, strict: bool = False):
        self.expected = expected
        self.strict = strict

    def __call__(self, text: str) -> tuple[bool, str]:
        cleaned = CleanAnswer(text)
        note = ""
        try:
            data = json.loads(cleaned)
        except ValueError:
            fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
            if self.strict or not fenced:
                return False, "reply is not valid JSON"
            try:
                data = json.loads(fenced.group(1))
            except ValueError:
                return False, "fenced reply is not valid JSON"
            note = "inside a code fence"
        if not ValueMatches(self.expected, data):
            return False, f"JSON does not match: got {json.dumps(data)[:200]}"
        return True, note

    def describe(self) -> str:
        return f"JSON {json.dumps(self.expected)}"

# the reply has some text, used for the speed tasks where only the timing matters
class NonEmpty:
    def __init__(self, min_words: int = 1):
        self.min_words = min_words

    def __call__(self, text: str) -> tuple[bool, str]:
        words = len(CleanAnswer(text).split())
        return (words >= self.min_words), (f"{words} words" if words < self.min_words else "")

    def describe(self) -> str:
        return f"at least {self.min_words} words"

# --- coding: the model's function is run against asserts the benchmark defines ---

# the only modules the model's code may import, and names it may not use. the code is checked before it runs,
# so a reply that reaches for files, processes, the network or python internals fails instead of running
SAFE_IMPORTS = {"math", "re", "itertools", "collections", "functools", "string", "typing", "heapq", "bisect", "operator"}
BLOCKED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars", "getattr",
                 "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview"}

def ExtractCode(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", CleanAnswer(text), re.DOTALL | re.IGNORECASE)
    return blocks[-1] if blocks else CleanAnswer(text)

# why this code may not run, or None when it only defines things and uses allowed modules
def UnsafeCode(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"syntax error: {e.msg} on line {e.lineno}"
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        if not (is_docstring or isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))):
            return f"top-level {type(node).__name__} on line {node.lineno}, only definitions are allowed"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] not in SAFE_IMPORTS for alias in node.names):
            return f"import of {[alias.name for alias in node.names]} is not allowed"
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in SAFE_IMPORTS:
            return f"import from {node.module} is not allowed"
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return f"use of {node.id} is not allowed"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"use of {node.attr} is not allowed"
    return None

HARNESS = """
namespace = {}
exec(compile(open("solution.py", encoding="utf-8").read(), "solution.py", "exec"), namespace)
FUNCTION = namespace[%r]
%s
print("ALL ASSERTS PASSED")
"""

# the reply defines `function` and it passes the asserts. the code runs in a separate python process in an empty
# temporary folder, isolated from the user's site packages and environment, and is stopped after `timeout` seconds
class CodePasses:
    def __init__(self, function: str, asserts: list[str], timeout: float = 10):
        self.function = function
        self.asserts = asserts
        self.timeout = timeout

    def __call__(self, text: str) -> tuple[bool, str]:
        code = ExtractCode(text)
        problem = UnsafeCode(code)
        if problem:
            return False, f"code not run, {problem}"
        with tempfile.TemporaryDirectory(prefix="cornilia_bench_") as folder:
            with open(os.path.join(folder, "solution.py"), "w", encoding="utf-8") as file:
                file.write(code)
            with open(os.path.join(folder, "check.py"), "w", encoding="utf-8") as file:
                file.write(HARNESS % (self.function, "\n".join(f"assert {line}, {line!r}" for line in self.asserts)))
            try:
                result = subprocess.run([sys.executable, "-I", "check.py"], cwd=folder, capture_output=True, text=True,
                                        timeout=self.timeout, env={"PATH": os.environ.get("PATH", "")})
            except subprocess.TimeoutExpired:
                return False, f"stopped after {self.timeout:g}s"
        if result.returncode == 0 and "ALL ASSERTS PASSED" in result.stdout:
            return True, ""
        error = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return False, error[:200]

    def describe(self) -> str:
        return f"{self.function} passes {len(self.asserts)} asserts"
