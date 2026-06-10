"""Tests for tool system — classifications, names, orchestration."""
import pytest

from config.config import Config
from services.tools.orchestration import ToolCallRequest, partition_tool_calls
from tools.builtin.FileEditTool import FileEditTool
from tools.builtin.GlobTool import GlobTool
from tools.builtin.GrepTool import GrepTool
from tools.builtin.FileReadTool import FileReadTool
from tools.builtin.BashTool import BashTool
from tools.builtin.WebFetchTool import WebFetchTool
from tools.builtin.WebSearchTool import WebSearchTool
from tools.builtin.FileWriteTool import FileWriteTool

config = Config()


# ── Tool names match Claude Code ──

def test_tool_names():
    names = {
        FileReadTool(config).name: "Read",
        FileEditTool(config).name: "Edit",
        FileWriteTool(config).name: "Write",
        BashTool(config).name: "Bash",
        GrepTool(config).name: "Grep",
        GlobTool(config).name: "Glob",
        WebSearchTool(config).name: "WebSearch",
        WebFetchTool(config).name: "WebFetch",
    }
    for actual, expected in names.items():
        assert actual == expected, f"Expected {expected}, got {actual}"


# ── Concurrency classification ──

@pytest.mark.parametrize("tool_cls,expected", [
    (FileReadTool, True),
    (GrepTool, True),
    (GlobTool, True),
    (WebSearchTool, True),
    (WebFetchTool, True),
    (FileEditTool, False),
    (FileWriteTool, False),
])
def test_concurrency_safe(tool_cls, expected):
    tool = tool_cls(config)
    assert tool.is_concurrency_safe({}) == expected


# ── Read-only classification ──

@pytest.mark.parametrize("tool_cls,expected", [
    (FileReadTool, True),
    (GrepTool, True),
    (GlobTool, True),
    (WebSearchTool, True),
    (WebFetchTool, True),
    (FileEditTool, False),
    (FileWriteTool, False),
])
def test_read_only(tool_cls, expected):
    tool = tool_cls(config)
    assert tool.is_read_only({}) == expected


# ── Shell conditional classification ──

def test_shell_read_only_commands():
    shell = BashTool(config)
    assert shell.is_read_only({"command": "ls -la"})
    assert shell.is_read_only({"command": "git status"})
    assert shell.is_read_only({"command": "grep foo | wc -l"})
    assert not shell.is_read_only({"command": "rm -rf /tmp"})
    assert not shell.is_read_only({"command": "mkdir foo"})


def test_shell_concurrency_follows_read_only():
    shell = BashTool(config)
    assert shell.is_concurrency_safe({"command": "ls"})
    assert not shell.is_concurrency_safe({"command": "npm install"})


def test_shell_destructive():
    shell = BashTool(config)
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
        "Read": FileReadTool(config),
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
        "Edit": FileEditTool(config),
        "Read": FileReadTool(config),
    }
    batches = partition_tool_calls(calls, tools)
    assert len(batches) == 3
    assert batches[0].is_concurrency_safe  # Grep+Glob parallel
    assert len(batches[0].calls) == 2
    assert not batches[1].is_concurrency_safe  # Edit serial
    assert batches[2].is_concurrency_safe  # Read is safe, just in its own batch after write


def test_max_result_size():
    # CC parity: per-tool maxResultSizeChars. Most tools default to 100k;
    # Grep is 20k and Bash is 30k (claude-code GrepTool.ts / BashTool.tsx).
    expected = {
        FileReadTool: 100_000,
        FileEditTool: 100_000,
        BashTool: 30_000,
        GrepTool: 20_000,
    }
    for tool_cls, limit in expected.items():
        assert tool_cls(config).max_result_size_chars == limit
