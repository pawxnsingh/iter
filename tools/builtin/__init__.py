from tools.base import Tool
from tools.builtin.agent_tool import AgentTool
from tools.builtin.ask_user import AskUserTool
from tools.builtin.config_tool import ConfigTool
from tools.builtin.edit_file import EditFileTool
from tools.builtin.glob import GlobTool
from tools.builtin.grep import GrepTool
from tools.builtin.list_dir import ListDirTool
from tools.builtin.memory import MemoryTool
from tools.builtin.notebook_edit import NotebookEditTool
from tools.builtin.plan_mode import EnterPlanModeTool, ExitPlanModeTool
from tools.builtin.read_file import ReadFileTool
from tools.builtin.send_message import SendMessageTool
from tools.builtin.shell import ShellTool
from tools.builtin.sleep_tool import SleepTool
from tools.builtin.task_tools import (
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
    TaskUpdateTool,
)
from tools.builtin.todo import TodosTool
from tools.builtin.tool_search import ToolSearchTool
from tools.builtin.web_fetch import WebFetchTool
from tools.builtin.web_search import WebSearchTool
from tools.builtin.write_file import WriteFileTool

__all__ = [
    "ReadFileTool", "WriteFileTool", "EditFileTool", "ShellTool",
    "ListDirTool", "GrepTool", "GlobTool", "WebSearchTool", "WebFetchTool",
    "TodosTool", "MemoryTool", "AskUserTool", "AgentTool",
    "EnterPlanModeTool", "ExitPlanModeTool", "ToolSearchTool", "SleepTool",
    "ConfigTool", "NotebookEditTool", "SendMessageTool",
    "TaskCreateTool", "TaskGetTool", "TaskListTool", "TaskUpdateTool", "TaskStopTool",
]


def get_all_builtin_tools() -> list[Tool]:
    return [
        ReadFileTool,
        WriteFileTool,
        EditFileTool,
        ShellTool,
        ListDirTool,
        GrepTool,
        GlobTool,
        WebSearchTool,
        WebFetchTool,
        TodosTool,
        MemoryTool,
        AskUserTool,
        AgentTool,
        EnterPlanModeTool,
        ExitPlanModeTool,
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
