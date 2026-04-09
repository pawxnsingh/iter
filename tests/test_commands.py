"""Tests for slash command system."""
import asyncio


from commands import execute_slash_command, get_all_commands, is_slash_command


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_is_slash_command():
    assert is_slash_command("/help")
    assert is_slash_command("/clear")
    assert not is_slash_command("hello")
    assert not is_slash_command("")


def test_all_commands_registered():
    cmds = get_all_commands()
    expected = {
        "help", "clear", "compact", "cost", "status", "config", "model",
        "memory", "exit", "resume", "plan", "diff",
        "tools", "permissions", "sessions", "tokens", "export",
        "doctor", "debug", "fast",
    }
    for name in expected:
        assert name in cmds, f"Missing command: /{name}"


def test_help_command():
    result = _run(execute_slash_command("/help", {}))
    assert "Available commands" in result


def test_unknown_command():
    result = _run(execute_slash_command("/nonexistent", {}))
    assert "Unknown command" in result


def test_exit_command():
    result = _run(execute_slash_command("/exit", {}))
    assert result == "__EXIT__"


def test_cost_without_context():
    result = _run(execute_slash_command("/cost", {}))
    assert "No context manager" in result
