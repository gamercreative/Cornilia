# tests for loading tools from one or several folders (CORNILIA_TOOLS_DIR / TOOLS_DIR)
import os
from pathlib import Path

import pytest

from cornilia import settings
from cornilia.errors import DuplicateToolError
from cornilia.tools import LoadTools


def write_tool(folder: Path, file_name: str, tool_name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    path.write_text(
        "from cornilia.tools import tool\n\n"
        "@tool\n"
        f"def {tool_name}() -> str:\n"
        f'    """Says which file it came from."""\n'
        f"    return {str(path)!r}\n",
        encoding="utf-8",
    )
    return path


def test_single_folder_loads_like_before(tmp_path):
    write_tool(tmp_path / "one", "greet.py", "greet")
    tools = LoadTools(tmp_path / "one")
    assert list(tools) == ["greet"]


def test_two_folders_are_merged(tmp_path):
    first = write_tool(tmp_path / "a", "tools.py", "alpha")
    second = write_tool(tmp_path / "b", "tools.py", "beta") # same file name in both folders on purpose
    tools = LoadTools([tmp_path / "a", tmp_path / "b"])
    assert sorted(tools) == ["alpha", "beta"]
    assert tools["alpha"]() == str(first)
    assert tools["beta"]() == str(second)


def test_same_tool_name_in_two_folders_names_both_files(tmp_path):
    first = write_tool(tmp_path / "a", "first.py", "clash")
    second = write_tool(tmp_path / "b", "second.py", "clash")
    with pytest.raises(DuplicateToolError) as error:
        LoadTools([tmp_path / "a", tmp_path / "b"])
    message = str(error.value)
    assert "clash" in message and str(first) in message and str(second) in message


def test_the_same_folder_listed_twice_is_loaded_once(tmp_path):
    write_tool(tmp_path / "a", "greet.py", "greet")
    assert list(LoadTools([tmp_path / "a", tmp_path / "a"])) == ["greet"]


def test_missing_folder_in_the_list_is_skipped_with_a_warning(tmp_path, capsys):
    write_tool(tmp_path / "a", "greet.py", "greet")
    tools = LoadTools([tmp_path / "a", tmp_path / "missing"])
    assert list(tools) == ["greet"]
    assert "does not exist" in capsys.readouterr().out


def test_tools_folders_setting_accepts_one_folder_a_joined_string_or_a_list():
    assert settings.ToolsFolders(None) == []
    assert settings.ToolsFolders("") == []
    assert settings.ToolsFolders("tools") == [Path("tools")]
    assert settings.ToolsFolders(f"a{os.pathsep}b{os.pathsep}") == [Path("a"), Path("b")]
    assert settings.ToolsFolders(["a", Path("b")]) == [Path("a"), Path("b")]


def test_create_tools_reads_every_configured_folder(tmp_path, monkeypatch):
    from cornilia import main
    write_tool(tmp_path / "a", "a.py", "alpha")
    write_tool(tmp_path / "b", "b.py", "beta")
    monkeypatch.setattr(settings, "TOOLS_DIR", f"{tmp_path / 'a'}{os.pathsep}{tmp_path / 'b'}")
    assert sorted(main.CreateTools()) == ["alpha", "beta"]
    monkeypatch.setattr(settings, "TOOLS_DIR", None)
    assert main.CreateTools() == {}
