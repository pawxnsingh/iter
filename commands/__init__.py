"""
Slash command system.

Source: src/commands/ (80+ command directories)

Commands are registered with a decorator and dispatched by name.
The TUI intercepts lines starting with '/' and routes them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_COMMANDS: dict[str, SlashCommand] = {}


_CTX_MISSING_MESSAGES = {
    "context_manager": "No context manager.",
    "session": "No session active.",
    "config": "No config available.",
}


def require_context(*keys: str):
    """Decorator: short-circuit a command if `ctx` is missing any required key.

    Usage:
        @register_command("xyz", "...")
        @require_context("context_manager")
        def cmd_xyz(args, ctx):
            ...
    """
    import asyncio
    import functools

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(args: str, ctx: dict):
                for key in keys:
                    if key not in ctx:
                        return _CTX_MISSING_MESSAGES.get(key, f"No {key} available.")
                return await fn(args, ctx)
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(args: str, ctx: dict):
            for key in keys:
                if key not in ctx:
                    return _CTX_MISSING_MESSAGES.get(key, f"No {key} available.")
            return fn(args, ctx)
        return wrapper

    return decorator


@dataclass
class SlashCommand:
    name: str
    description: str
    handler: Callable


def register_command(name: str, description: str):
    """Decorator to register a slash command."""
    def decorator(fn):
        _COMMANDS[name] = SlashCommand(name=name, description=description, handler=fn)
        return fn
    return decorator


def is_slash_command(text: str) -> bool:
    """Check if text is a slash command."""
    return text.strip().startswith("/")


def get_command(name: str) -> SlashCommand | None:
    """Get a command by name."""
    return _COMMANDS.get(name)


def get_all_commands() -> dict[str, SlashCommand]:
    """Get all registered commands."""
    return dict(_COMMANDS)


async def execute_slash_command(text: str, context: dict[str, Any]) -> str | None:
    """
    Parse and execute a slash command.
    Returns the response string, or None if unknown.
    """
    parts = text.strip().split(maxsplit=1)
    cmd_name = parts[0][1:]
    args = parts[1] if len(parts) > 1 else ""

    cmd = _COMMANDS.get(cmd_name)
    if not cmd:
        available = ", ".join(f"/{n}" for n in sorted(_COMMANDS.keys()))
        return f"Unknown command: /{cmd_name}\nAvailable: {available}"

    try:
        import asyncio
        if asyncio.iscoroutinefunction(cmd.handler):
            return await cmd.handler(args, context)
        else:
            return cmd.handler(args, context)
    except Exception as e:
        return f"Command error: {e}"


async def _run_git(*args: str, merge_stderr: bool = False) -> tuple[int, str, str]:
    """Run `git <args>` and return (returncode, stdout, stderr).

    Centralises the subprocess boilerplate that every /git, /diff, /log,
    /branch, /stash command was duplicating.

    `merge_stderr=True` redirects stderr into stdout (used by /git for
    arbitrary subcommands where errors should surface in the same stream).
    """
    import asyncio

    stderr_target = (
        asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE
    )
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr_target,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode(errors="replace").strip(),
        (stderr.decode(errors="replace").strip() if stderr is not None else ""),
    )


@register_command("help", "Show available commands")
def cmd_help(args: str, ctx: dict) -> str:
    lines = ["Available commands:", ""]
    for name, cmd in sorted(_COMMANDS.items()):
        lines.append(f"  /{name:<15} {cmd.description}")
    return "\n".join(lines)


@register_command("clear", "Clear conversation history")
def cmd_clear(args: str, ctx: dict) -> str:
    if "context_manager" in ctx:
        ctx["context_manager"].clear()
    return "Conversation cleared."


@register_command("compact", "Force context compaction")
async def cmd_compact(args: str, ctx: dict) -> str:
    if "context_manager" in ctx:
        cm = ctx["context_manager"]
        await cm.get_messages_with_compaction()
        stats = cm.get_token_stats()
        return f"Compacted. Tokens: {stats['current_tokens']:,} / {stats['context_window']:,}"
    return "No context manager available."


@register_command("cost", "Show token usage and cost")
def cmd_cost(args: str, ctx: dict) -> str:
    if "context_manager" in ctx:
        stats = ctx["context_manager"].get_token_stats()
        return (
            f"Tokens: {stats['current_tokens']:,} / {stats['context_window']:,} "
            f"({stats['percent_used']}% used)\n"
            f"Messages: {stats['messages']}\n"
            f"Compacted: {stats['compacted']}\n"
            f"Compact threshold: {stats['compact_threshold']:,}"
        )
    return "No context manager available."


@register_command("status", "Show session status")
def cmd_status(args: str, ctx: dict) -> str:
    lines = []
    if "session" in ctx:
        stats = ctx["session"].get_stats()
        lines.append(f"Session: {stats['session_id'][:8]}...")
        lines.append(f"Turns: {stats['turn_count']}")
        lines.append(f"Tools: {stats['tools_count']}")
    if "config" in ctx:
        lines.append(f"Model: {ctx['config'].model_name}")
        lines.append(f"CWD: {ctx['config'].cwd}")
    return "\n".join(lines) if lines else "No session active."


@register_command("config", "Show current configuration")
@require_context("config")
def cmd_config(args: str, ctx: dict) -> str:
    config = ctx["config"]
    return (
        f"Model: {config.model_name}\n"
        f"Temperature: {config.temperature}\n"
        f"Context Window: {config.model.context_window:,}\n"
        f"Max Turns: {config.max_turns}\n"
        f"CWD: {config.cwd}\n"
        f"MCP Servers: {len(config.mcp_servers)}\n"
        f"Debug: {config.debug}"
    )


@register_command("model", "Switch model (usage: /model <name>)")
def cmd_model(args: str, ctx: dict) -> str:
    if not args.strip():
        if "config" in ctx:
            return f"Current model: {ctx['config'].model_name}\nUsage: /model <name>"
        return "Usage: /model <name>"
    if "config" in ctx:
        old = ctx["config"].model_name
        ctx["config"].model_name = args.strip()
        return f"Model switched: {old} → {args.strip()}"
    return "No config available."


@register_command("memory", "Show stored memories")
def cmd_memory(args: str, ctx: dict) -> str:
    import json

    from config.loader import get_data_dir
    path = get_data_dir() / "user_memory.json"
    if not path.exists():
        return "No memories stored."
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", {})
        if not entries:
            return "No memories stored."
        lines = ["Stored memories:", ""]
        for key, value in entries.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading memory: {e}"


@register_command("tools", "List available tools")
@require_context("session")
def cmd_tools(args: str, ctx: dict) -> str:
    tools = ctx["session"].tool_registry.get_tools()
    lines = [f"Available tools ({len(tools)}):", ""]
    for t in sorted(tools, key=lambda x: x.name):
        ro = "read" if t.is_read_only({}) else "write"
        safe = "parallel" if t.is_concurrency_safe({}) else "serial"
        lines.append(f"  {t.name:<15} [{ro}/{safe}]  {t.description[:60]}...")
    return "\n".join(lines)


@register_command("permissions", "Show/modify permission rules")
@require_context("config")
def cmd_permissions(args: str, ctx: dict) -> str:
    config = ctx["config"]
    lines = [
        f"Permission mode: {config.permission_mode}",
        "",
        "Available modes:",
        "  default      - Ask for writes, auto-allow reads",
        "  plan         - Block ALL tool execution",
        "  bypass       - Allow everything (--permission-mode bypass)",
        "  accept_edits - Auto-approve edits in working directory",
        "  dont_ask     - Auto-deny unmatched, no prompts",
        "",
        "Change: /permissions <mode>",
    ]
    if args.strip():
        mode = args.strip()
        valid = {"default", "plan", "bypass", "accept_edits", "dont_ask"}
        if mode in valid:
            config.permission_mode = mode
            return f"Permission mode changed to: {mode}"
        return f"Invalid mode. Choose from: {', '.join(sorted(valid))}"
    return "\n".join(lines)


@register_command("sessions", "List previous sessions")
@require_context("config")
def cmd_sessions(args: str, ctx: dict) -> str:
    from session.storage import SessionStorage
    sessions = SessionStorage.list_sessions(str(ctx["config"].cwd))
    if not sessions:
        return "No previous sessions."
    lines = ["Previous sessions:", ""]
    for i, s in enumerate(sessions[:15]):
        lines.append(f"  {i+1}. {s.session_id[:8]}... | {s.updated_at[:19]} | {s.turn_count} turns")
    return "\n".join(lines)


@register_command("tokens", "Detailed token breakdown")
@require_context("context_manager")
def cmd_tokens(args: str, ctx: dict) -> str:
    stats = ctx["context_manager"].get_token_stats()
    lines = [
        "Token Usage:",
        f"  Current:   {stats['current_tokens']:>10,} tokens",
        f"  Window:    {stats['context_window']:>10,} tokens",
        f"  Threshold: {stats['compact_threshold']:>10,} tokens",
        f"  Used:      {stats['percent_used']:>9}%",
        f"  Left:      {stats['percent_left']:>9}%",
        f"  Messages:  {stats['messages']:>10}",
        "",
        f"  Warning:   {'YES' if stats['above_warning'] else 'no'}",
        f"  Error:     {'YES' if stats['above_error'] else 'no'}",
        f"  Compact:   {'YES' if stats['above_compact'] else 'no'}",
        f"  Compacted: {'yes' if stats['compacted'] else 'no'}",
    ]
    return "\n".join(lines)


@register_command("export", "Export conversation transcript")
@require_context("session")
def cmd_export(args: str, ctx: dict) -> str:
    session = ctx["session"]
    path = session.storage.path
    if path.exists():
        return f"Transcript saved at:\n  {path}\n\nUse 'cat {path}' to view."
    return "No transcript available yet."


@register_command("doctor", "Diagnose common issues")
def cmd_doctor(args: str, ctx: dict) -> str:
    import os
    lines = ["Diagnostics:", ""]

    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_azure = bool(os.environ.get("AZURE_OPENAI_API_KEY"))
    lines.append(f"  Anthropic API key: {'set' if has_anthropic else 'MISSING'}")
    lines.append(f"  OpenAI API key:    {'set' if has_openai else 'MISSING'}")
    lines.append(f"  Azure API key:     {'set' if has_azure else 'MISSING'}")

    if not any([has_anthropic, has_openai, has_azure]):
        lines.append("")
        lines.append("  !! No LLM provider configured.")
        lines.append("  Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY")

    if "config" in ctx:
        config = ctx["config"]
        lines.append(f"  Model:           {config.model_name}")
        lines.append(f"  CWD exists:      {'yes' if config.cwd.exists() else 'NO'}")
        lines.append(f"  Permission mode: {config.permission_mode}")

    if "session" in ctx:
        tools = ctx["session"].tool_registry.get_tools()
        lines.append(f"  Tools loaded:    {len(tools)}")

    from utils.paths import find_git_root
    if "config" in ctx:
        is_git = find_git_root(ctx["config"].cwd) is not None
        lines.append(f"  Git repo:        {'yes' if is_git else 'no'}")

    return "\n".join(lines)


@register_command("debug", "Toggle debug logging")
def cmd_debug(args: str, ctx: dict) -> str:
    import logging
    if "config" in ctx:
        ctx["config"].debug = not ctx["config"].debug
        level = logging.DEBUG if ctx["config"].debug else logging.WARNING
        logging.basicConfig(level=level, force=True)
        return f"Debug: {'ON' if ctx['config'].debug else 'OFF'}"
    return "No config."


@register_command("fast", "Toggle fast mode (shorter responses)")
def cmd_fast(args: str, ctx: dict) -> str:
    return "Fast mode is controlled by the model. Use /model to switch to a faster model."


@register_command("plan", "Toggle plan mode (blocks all tool execution)")
@require_context("config")
def cmd_plan(args: str, ctx: dict) -> str:
    config = ctx["config"]
    if config.permission_mode == "plan":
        config.permission_mode = "default"
        return "Plan mode OFF. Tools will execute normally."
    else:
        config.permission_mode = "plan"
        return "Plan mode ON. All tool execution is blocked. Use /plan again to exit."


@register_command("diff", "Show uncommitted git changes")
async def cmd_diff(args: str, ctx: dict) -> str:
    try:
        _, output, _ = await _run_git("diff", "--stat")
    except Exception as e:
        return f"Git not available: {e}"
    return f"Uncommitted changes:\n{output}" if output else "No uncommitted changes."


@register_command("review", "Code review mode — review recent changes")
async def cmd_review(args: str, ctx: dict) -> str:
    try:
        _, staged, _ = await _run_git("diff", "--cached", "--stat")
        _, unstaged, _ = await _run_git("diff", "--stat")
    except Exception as e:
        return f"Git not available: {e}"

    lines = []
    if staged:
        lines.append("Staged changes:")
        lines.append(staged)
    if unstaged:
        lines.append("\nUnstaged changes:")
        lines.append(unstaged)
    if not lines:
        return "No changes to review."
    return "\n".join(lines) + "\n\nAsk me to review specific files or all changes."


@register_command("branch", "Show or switch git branch")
async def cmd_branch(args: str, ctx: dict) -> str:
    target = args.strip()
    if target:
        rc, _, stderr = await _run_git("checkout", target)
        return f"Switched to branch: {target}" if rc == 0 else f"Failed: {stderr}"

    _, current, _ = await _run_git("branch", "--show-current")
    return f"Current branch: {current}" if current else "Not a git repository."


@register_command("log", "Show recent git log")
async def cmd_log(args: str, ctx: dict) -> str:
    n = args.strip() or "10"
    try:
        _, output, _ = await _run_git("log", "--oneline", f"-{n}")
    except Exception as e:
        return f"Git not available: {e}"
    return output or "No git history."


@register_command("undo", "Undo the last assistant message")
@require_context("context_manager")
def cmd_undo(args: str, ctx: dict) -> str:
    cm = ctx["context_manager"]
    removed = 0
    while cm._messages and cm._messages[-1].role in ("assistant", "tool"):
        cm._messages.pop()
        removed += 1
    if removed:
        return f"Undone: removed {removed} messages from last turn."
    return "Nothing to undo."


@register_command("retry", "Retry the last message")
@require_context("context_manager")
def cmd_retry(args: str, ctx: dict) -> str:
    cm = ctx["context_manager"]
    last_user = None
    for msg in reversed(cm._messages):
        if msg.role == "user":
            last_user = msg.content
            break
    if not last_user:
        return "No previous message to retry."
    while cm._messages and cm._messages[-1].role != "user":
        cm._messages.pop()
    if cm._messages:
        cm._messages.pop()
    return f"__RETRY__:{last_user}"


@register_command("mcp", "Show MCP server status")
@require_context("session")
def cmd_mcp(args: str, ctx: dict) -> str:
    mcp = ctx["session"].mcp_manager
    if not mcp.config.mcp_servers:
        return "No MCP servers configured."
    live = {s["name"]: s for s in mcp.get_all_servers()}
    lines = ["MCP Servers:", ""]
    for name, server_config in mcp.config.mcp_servers.items():
        enabled = "enabled" if server_config.enabled else "disabled"
        transport = "stdio" if server_config.command else "http"
        status = live.get(name)
        if status:
            lines.append(
                f"  {name}: {transport} ({enabled}) — "
                f"{status['status']}, {status['tools']} tools"
            )
        else:
            lines.append(f"  {name}: {transport} ({enabled})")
    return "\n".join(lines)


@register_command("context", "Show context window usage visualization")
@require_context("context_manager")
def cmd_context(args: str, ctx: dict) -> str:
    stats = ctx["context_manager"].get_token_stats()
    pct = stats["percent_used"]
    bar_len = 40
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    return (
        f"Context: [{bar}] {pct}%\n"
        f"  {stats['current_tokens']:,} / {stats['context_window']:,} tokens\n"
        f"  {stats['messages']} messages | Compact at {stats['compact_threshold']:,}"
    )


@register_command("files", "List recently read/edited files this session")
@require_context("context_manager")
def cmd_files(args: str, ctx: dict) -> str:
    cm = ctx["context_manager"]
    paths = set()
    for msg in cm._messages:
        if msg.role == "tool" and msg.tool_call_id:
            pass
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                func = tc.get("function", {})
                args_str = func.get("arguments", "{}")
                try:
                    import json
                    a = json.loads(args_str) if isinstance(args_str, str) else args_str
                    if isinstance(a, dict) and "path" in a:
                        paths.add(a["path"])
                except Exception:
                    pass
    if not paths:
        return "No files accessed this session."
    lines = ["Files accessed this session:", ""]
    for p in sorted(paths):
        lines.append(f"  {p}")
    return "\n".join(lines)


@register_command("env", "Show environment variables (filtered)")
def cmd_env(args: str, ctx: dict) -> str:
    import os
    skip = {"KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"}
    lines = ["Environment (sensitive values hidden):", ""]
    for k, v in sorted(os.environ.items()):
        if any(s in k.upper() for s in skip):
            lines.append(f"  {k}=***")
        else:
            val = v[:60] + "..." if len(v) > 60 else v
            lines.append(f"  {k}={val}")
    return "\n".join(lines[:50]) + (f"\n  ... ({len(os.environ)} total)" if len(os.environ) > 50 else "")


@register_command("version", "Show version information")
def cmd_version(args: str, ctx: dict) -> str:
    import platform
    return (
        f"Newt v0.1.0\n"
        f"Python {platform.python_version()}\n"
        f"Platform: {platform.system()} {platform.release()}"
    )


@register_command("providers", "Show available LLM providers")
def cmd_providers(args: str, ctx: dict) -> str:
    import os
    lines = ["LLM Providers:", ""]
    providers = [
        ("Anthropic", "ANTHROPIC_API_KEY"),
        ("OpenAI", "OPENAI_API_KEY"),
        ("Azure OpenAI", "AZURE_OPENAI_API_KEY"),
    ]
    for name, env_key in providers:
        has_key = bool(os.environ.get(env_key))
        status = "✓ configured" if has_key else "✗ not set"
        lines.append(f"  {name:<25} {status}")
    if "config" in ctx:
        lines.append(f"\n  Active: {ctx['config'].model_name}")
    lines.append("\n  Set LLM_PROVIDER env to force a provider (anthropic/openai/azure)")
    return "\n".join(lines)


@register_command("usage", "Show token usage for this session")
@require_context("context_manager")
def cmd_usage(args: str, ctx: dict) -> str:
    stats = ctx["context_manager"].get_token_stats()
    return (
        f"Session Usage:\n"
        f"  Context tokens: {stats['current_tokens']:,}\n"
        f"  Context window: {stats['context_window']:,}\n"
        f"  Usage: {stats['percent_used']}%\n"
        f"  Messages: {stats['messages']}\n"
        f"  Compacted: {'yes' if stats['compacted'] else 'no'}"
    )


@register_command("keybindings", "Show keyboard shortcuts")
def cmd_keybindings(args: str, ctx: dict) -> str:
    return (
        "Keyboard shortcuts:\n"
        "  Ctrl+C      Cancel current operation\n"
        "  Ctrl+D      Exit (when input empty)\n"
        "  ! <cmd>     Run shell command inline\n"
        "  / <cmd>     Run slash command\n"
        "  Enter       Submit message"
    )


@register_command("agents", "List available agent types")
def cmd_agents(args: str, ctx: dict) -> str:
    from tools.builtin.AgentTool.AgentTool import BUILT_IN_AGENTS
    lines = ["Available agent types:", ""]
    for name, defn in sorted(BUILT_IN_AGENTS.items()):
        tools = ", ".join(defn.tools) if defn.tools else "all"
        lines.append(f"  {name:<20} {defn.description}")
        lines.append(f"  {'':20} tools: {tools} | max turns: {defn.max_turns}")
    return "\n".join(lines)


@register_command("tasks", "Show current task list")
@require_context("session")
def cmd_tasks(args: str, ctx: dict) -> str:
    tool = ctx["session"].tool_registry.get("TodoWrite")
    if not tool or not hasattr(tool, 'get_todos'):
        return "No task tracking tool available."
    todos = tool.get_todos()
    if not todos:
        return "No tasks."
    lines = ["Tasks:", ""]
    for t in todos:
        icon = {"pending": "○", "in_progress": "◐", "completed": "●"}.get(t["status"], "?")
        lines.append(f"  {icon} [{t['status']}] {t['content']}")
    return "\n".join(lines)


@register_command("share", "Share session transcript (copy path)")
@require_context("session")
def cmd_share(args: str, ctx: dict) -> str:
    path = ctx["session"].storage.path
    if path.exists():
        size = path.stat().st_size
        return f"Session transcript:\n  {path}\n  Size: {size:,} bytes\n\nShare this file for debugging."
    return "No transcript yet."


@register_command("export-md", "Export session as markdown")
@require_context("session")
async def cmd_export_md(args: str, ctx: dict) -> str:
    from session.export import export_session_to_markdown
    path = ctx["session"].storage.path
    if not path.exists():
        return "No transcript yet."
    md = export_session_to_markdown(path)
    export_path = path.with_suffix(".md")
    export_path.write_text(md, encoding="utf-8")
    return f"Exported to: {export_path}"


@register_command("stash", "Stash current git changes")
async def cmd_stash(args: str, ctx: dict) -> str:
    action = args.strip() or "push"
    _, stdout, stderr = await _run_git("stash", action)
    return stdout or stderr or "Stash operation completed."


@register_command("git", "Run a git command")
async def cmd_git(args: str, ctx: dict) -> str:
    import shlex
    if not args.strip():
        return "Usage: /git <command> (e.g., /git status, /git log --oneline -5)"
    try:
        git_args = shlex.split(args.strip())
    except ValueError as e:
        return f"Invalid arguments: {e}"
    _, output, _ = await _run_git(*git_args, merge_stderr=True)
    return output or "No output."


@register_command("theme", "Show available themes")
def cmd_theme(args: str, ctx: dict) -> str:
    return (
        "Available themes:\n"
        "  dark (default)\n"
        "  light\n\n"
        "Theme switching is not yet implemented."
    )


@register_command("vim", "Toggle vim keybindings")
def cmd_vim(args: str, ctx: dict) -> str:
    return "Vim keybindings are not yet implemented."


@register_command("exit", "Exit the session")
def cmd_exit(args: str, ctx: dict) -> str:
    return "__EXIT__"


@register_command("resume", "Resume previous session")
@require_context("config")
def cmd_resume(args: str, ctx: dict) -> str:
    from session.storage import SessionStorage
    cwd = str(ctx["config"].cwd)
    sessions = SessionStorage.list_sessions(cwd)
    if not sessions:
        return "No previous sessions found."

    if args.strip():
        try:
            idx = int(args.strip()) - 1
            if 0 <= idx < len(sessions):
                target = sessions[idx]
                return _do_resume(target, ctx)
            else:
                return f"Invalid session number. Use 1-{len(sessions)}"
        except ValueError:
            for s in sessions:
                if s.session_id.startswith(args.strip()):
                    return _do_resume(s, ctx)
            return f"No session matching '{args.strip()}'"

    lines = ["Previous sessions (use /resume <number> to restore):", ""]
    for i, s in enumerate(sessions[:10]):
        lines.append(f"  {i+1}. {s.session_id[:8]}... | {s.updated_at[:19]} | {s.turn_count} turns | {s.message_count} msgs")
    return "\n".join(lines)


def _do_resume(session_meta, ctx: dict) -> str:
    """Actually resume a session by loading its transcript into context."""
    import hashlib
    from pathlib import Path

    from platformdirs import user_config_dir

    from session.storage import SessionStorage

    slug = hashlib.md5(session_meta.project_dir.encode()).hexdigest()[:16]
    transcript_path = (
        Path(user_config_dir("ai-agent")) / "projects" / slug
        / "transcripts" / f"{session_meta.session_id}.jsonl"
    )

    if not transcript_path.exists():
        return f"Transcript file not found: {transcript_path}"

    messages = SessionStorage.load_transcript(transcript_path)
    if not messages:
        return "Transcript is empty."

    context_manager = ctx.get("context_manager")
    if not context_manager:
        return "No context manager available."

    context_manager.clear()

    last_summary_idx = -1
    for i, m in enumerate(messages):
        if m.get("type") == "summary" and m.get("summary"):
            last_summary_idx = i

    loaded = 0
    if last_summary_idx >= 0:
        from services.compact.prompt import get_compact_user_summary_message
        summary_msg = get_compact_user_summary_message(
            messages[last_summary_idx].get("summary", ""),
            suppress_follow_up_questions=True,
            transcript_path=str(transcript_path),
        )
        context_manager.add_user_message(summary_msg)
        loaded += 1
        replay = messages[last_summary_idx + 1:]
    else:
        replay = messages

    answered_ids = {
        m.get("tool_call_id")
        for m in replay
        if m.get("role") == "tool" and m.get("tool_call_id")
    }

    for msg in replay:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            context_manager.add_user_message(content)
            loaded += 1
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            kept_calls = [
                tc for tc in tool_calls if tc.get("id") in answered_ids
            ]
            if tool_calls and not kept_calls and not (content or "").strip():
                continue
            context_manager.add_assistant_message(
                content=content,
                tool_calls=kept_calls or None,
            )
            loaded += 1
        elif role == "tool":
            context_manager.add_tool_result(
                tool_call_id=msg.get("tool_call_id", ""),
                content=content,
            )
            loaded += 1

    session = ctx.get("session")
    if session is not None:
        new_storage = SessionStorage(session_meta.session_id, session_meta.project_dir)
        new_storage._last_uuid = next(
            (m["uuid"] for m in reversed(messages) if m.get("uuid")), None
        )
        session.storage = new_storage
        session.session_id = session_meta.session_id

    return (
        f"Resumed session {session_meta.session_id[:8]}...\n"
        f"Loaded {loaded} messages from {session_meta.turn_count} turns.\n"
        f"Continue from where you left off."
    )
