"""Tests for system prompt structure."""

from config.config import Config
from prompts.system import get_system_prompt
from tools.builtin.read_file import ReadFileTool
from tools.builtin.shell import ShellTool

config = Config()


def test_all_sections_present():
    tools = [ReadFileTool(config), ShellTool(config)]
    prompt = get_system_prompt(config, None, tools)

    sections = [
        "# System",
        "# Doing tasks",
        "# Executing actions with care",
        "# Using your tools",
        "# Tone and style",
        "# Output efficiency",
        "# Environment",
    ]
    for section in sections:
        assert section in prompt, f"Missing section: {section}"


def test_tool_guidance():
    tools = [ReadFileTool(config)]
    prompt = get_system_prompt(config, None, tools)
    assert "Read instead of cat" in prompt
    assert "Edit instead of sed" in prompt
    assert "Glob instead of find" in prompt


def test_environment_info():
    prompt = get_system_prompt(config, None, None)
    assert "Primary working directory" in prompt
    assert "Platform" in prompt
    assert "Shell" in prompt


def test_memory_included():
    prompt = get_system_prompt(config, "user prefers dark mode", None)
    assert "user prefers dark mode" in prompt


def test_developer_instructions_included():
    c = Config(developer_instructions="Always use TypeScript")
    prompt = get_system_prompt(c, None, None)
    assert "Always use TypeScript" in prompt
    assert "Project Instructions" in prompt


def test_no_emojis_instruction():
    prompt = get_system_prompt(config, None, None)
    assert "emoji" in prompt.lower()
