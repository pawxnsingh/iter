"""
Built-in tools — each tool lives in its own directory matching Claude Code's
structure (tools/<ToolName>Tool/<ToolName>Tool.py, plus prompt.py for the
tools that ship verbatim Claude Code model-facing guidance).

Re-exports here exist for backward compatibility — existing call sites still
import classes from `tools.builtin`. New code should prefer
`from tools.builtin.<ToolName>Tool import <ClassName>` directly.
"""

from tools.base import Tool
from tools.builtin.AgentResultTool import AgentResultTool
from tools.builtin.AgentTool import AgentTool
from tools.builtin.AskUserQuestionTool import AskUserQuestionTool
from tools.builtin.BashTool import BashTool
from tools.builtin.ConfigTool import ConfigTool
from tools.builtin.EnterPlanModeTool import EnterPlanModeTool
from tools.builtin.EnterWorktreeTool import EnterWorktreeTool
from tools.builtin.ExitPlanModeTool import ExitPlanModeTool
from tools.builtin.ExitWorktreeTool import ExitWorktreeTool
from tools.builtin.FileEditTool import FileEditTool
from tools.builtin.FileReadTool import FileReadTool
from tools.builtin.FileWriteTool import FileWriteTool
from tools.builtin.GlobTool import GlobTool
from tools.builtin.GrepTool import GrepTool
from tools.builtin.NotebookEditTool import NotebookEditTool
from tools.builtin.SendMessageTool import SendMessageTool
from tools.builtin.SleepTool import SleepTool
from tools.builtin.TaskCreateTool import TaskCreateTool
from tools.builtin.TaskGetTool import TaskGetTool
from tools.builtin.TaskListTool import TaskListTool
from tools.builtin.TaskStopTool import TaskStopTool
from tools.builtin.TaskUpdateTool import TaskUpdateTool
from tools.builtin.TodoWriteTool import TodoWriteTool
from tools.builtin.ToolSearchTool import ToolSearchTool
from tools.builtin.WebFetchTool import WebFetchTool
from tools.builtin.WebSearchTool import WebSearchTool

__all__ = [
    "FileReadTool", "FileWriteTool", "FileEditTool", "BashTool",
    "GrepTool", "GlobTool", "WebSearchTool", "WebFetchTool",
    "TodoWriteTool", "AskUserQuestionTool", "AgentTool", "AgentResultTool",
    "EnterPlanModeTool", "ExitPlanModeTool", "EnterWorktreeTool", "ExitWorktreeTool",
    "ToolSearchTool", "SleepTool",
    "ConfigTool", "NotebookEditTool", "SendMessageTool",
    "TaskCreateTool", "TaskGetTool", "TaskListTool", "TaskUpdateTool", "TaskStopTool",
]


def get_all_builtin_tools() -> list[Tool]:
    return [
        FileReadTool,
        FileWriteTool,
        FileEditTool,
        BashTool,
        GrepTool,
        GlobTool,
        WebSearchTool,
        WebFetchTool,
        TodoWriteTool,
        AskUserQuestionTool,
        AgentTool,
        AgentResultTool,
        EnterPlanModeTool,
        ExitPlanModeTool,
        EnterWorktreeTool,
        ExitWorktreeTool,
        ToolSearchTool,
        SleepTool,
        ConfigTool,
        NotebookEditTool,
        SendMessageTool,
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskUpdateTool,
        TaskStopTool,
    ]
