"""Tests for tool system — classifications, names, orchestration."""
import pytest

from config.config import Config
from services.tools.orchestration import ToolCallRequest, partition_tool_calls
from tools.builtin.edit_file import EditFileTool
from tools.builtin.glob import GlobTool
from tools.builtin.grep import GrepTool
from tools.builtin.list_dir import ListDirTool
from tools.builtin.read_file import ReadFileTool
from tools.builtin.shell import ShellTool
from tools.builtin.web_fetch import WebFetchTool
from tools.builtin.web_search import WebSearchTool
from tools.builtin.write_file import WriteFileTool

config = Config()


# ── Tool names match Claude Code ──

def test_tool_names():
    names = {
        ReadFileTool(config).name: "Read",
        EditFileTool(config).name: "Edit",
        WriteFileTool(config).name: "Write",
        ShellTool(config).name: "Bash",
        GrepTool(config).name: "Grep",
        GlobTool(config).name: "Glob",
        ListDirTool(config).name: "ListDir",
        WebSearchTool(config).name: "WebSearch",
        WebFetchTool(config).name: "WebFetch",
    }
    for actual, expected in names.items():
        assert actual == expected, f"Expected {expected}, got {actual}"


# ── Concurrency classification ──

@pytest.mark.parametrize("tool_cls,expected", [
    (ReadFileTool, True),
    (GrepTool, True),
    (GlobTool, True),
    (ListDirTool, True),
    (WebSearchTool, True),
    (WebFetchTool, True),
    (EditFileTool, False),
    (WriteFileTool, False),
])
def test_concurrency_safe(tool_cls, expected):
    tool = tool_cls(config)
    assert tool.is_concurrency_safe({}) == expected


# ── Read-only classification ──

@pytest.mark.parametrize("tool_cls,expected", [
    (ReadFileTool, True),
    (GrepTool, True),
    (GlobTool, True),
    (ListDirTool, True),
    (WebSearchTool, True),
    (WebFetchTool, True),
    (EditFileTool, False),
    (WriteFileTool, False),
])
def test_read_only(tool_cls, expected):
    tool = tool_cls(config)
    assert tool.is_read_only({}) == expected


# ── Shell conditional classification ──

def test_shell_read_only_commands():
    shell = ShellTool(config)
    assert shell.is_read_only({"command": "ls -la"})
    assert shell.is_read_only({"command": "git status"})
    assert shell.is_read_only({"command": "grep foo | wc -l"})
    assert not shell.is_read_only({"command": "rm -rf /tmp"})
    assert not shell.is_read_only({"command": "mkdir foo"})


def test_shell_concurrency_follows_read_only():
    shell = ShellTool(config)
    assert shell.is_concurrency_safe({"command": "ls"})
    assert not shell.is_concurrency_safe({"command": "npm install"})


def test_shell_destructive():
    shell = ShellTool(config)
    assert shell.is_destructive({"command": "rm -rf /tmp"})
    assert shell.is_destructive({"command": "git push --force"})
    assert not shell.is_destructive({"command": "ls"})


# ── Tool orchestration partitioning ──

def test_partition_parallel_reads():
    calls = [
        ToolCallRequest("c1", "Grep", {}),
        ToolCallRequest("c2", "Glob", {}),
        ToolCallRequest("c3", "Read", {}),
    ]
    tools = {
        "Grep": GrepTool(config),
        "Glob": GlobTool(config),
        "Read": ReadFileTool(config),
    }
    batches = partition_tool_calls(calls, tools)
    assert len(batches) == 1  # all in one parallel batch
    assert batches[0].is_concurrency_safe
    assert len(batches[0].calls) == 3


def test_partition_mixed():
    calls = [
        ToolCallRequest("c1", "Grep", {}),
        ToolCallRequest("c2", "Glob", {}),
        ToolCallRequest("c3", "Edit", {}),
        ToolCallRequest("c4", "Read", {}),
    ]
    tools = {
        "Grep": GrepTool(config),
        "Glob": GlobTool(config),
        "Edit": EditFileTool(config),
        "Read": ReadFileTool(config),
    }
    batches = partition_tool_calls(calls, tools)
    assert len(batches) == 3
    assert batches[0].is_concurrency_safe  # Grep+Glob parallel
    assert len(batches[0].calls) == 2
    assert not batches[1].is_concurrency_safe  # Edit serial
    assert batches[2].is_concurrency_safe  # Read is safe, just in its own batch after write


def test_max_result_size():
    for tool_cls in [ReadFileTool, EditFileTool, ShellTool, GrepTool]:
        tool = tool_cls(config)
        assert tool.max_result_size_chars == 100_000
