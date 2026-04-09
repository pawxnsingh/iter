"""
PieAgent CLI — Interactive Rich TUI matching Claude Code visual style.
"""

import asyncio
import logging
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from agent.agent import Agent
from agent.event import AgentEventType
from commands import execute_slash_command, is_slash_command
from config.loader import load_config
from ui.tui import TUI, get_console

load_dotenv()

console = get_console()


class PieAgent:
    def __init__(self, config):
        self.config = config
        self.agent = None
        self.tui = TUI(console=console, config=config)

    async def _permission_prompt(self, tool_name, message, is_destructive):
        return self.tui.show_permission_dialog(tool_name, message, is_destructive)

    def _setup_readline(self):
        """Set up readline for command history and tab completion."""
        try:
            import readline
            from pathlib import Path

            from platformdirs import user_config_dir

            # History file
            history_dir = Path(user_config_dir("pieagent"))
            history_dir.mkdir(parents=True, exist_ok=True)
            history_file = history_dir / "history"

            try:
                readline.read_history_file(str(history_file))
            except FileNotFoundError:
                pass

            readline.set_history_length(1000)

            import atexit
            atexit.register(readline.write_history_file, str(history_file))

            # Tab completion for slash commands
            from commands import get_all_commands
            cmd_names = ["/" + n for n in get_all_commands().keys()]

            def completer(text, state):
                if text.startswith("/"):
                    matches = [c for c in cmd_names if c.startswith(text)]
                else:
                    matches = []
                return matches[state] if state < len(matches) else None

            readline.set_completer(completer)
            readline.parse_and_bind("tab: complete")

        except ImportError:
            pass  # readline not available on all platforms

    async def run(self):
        self.tui.print_welcome(
            title="PieAgent",
            lines=[
                f"model: {self.config.model_name}",
                f"cwd: {self.config.cwd}",
                "type /help for commands, /exit to quit",
            ],
        )

        # Set up readline for command history + tab completion
        self._setup_readline()

        async with Agent(self.config, permission_prompt_fn=self._permission_prompt) as agent:
            self.agent = agent

            while True:
                try:
                    user_input = input("\n❯ ").strip()
                    if not user_input:
                        continue

                    # Reprint the user's input with highlighted background strip
                    # (like Claude Code's off-white user message bar)
                    width = console.width
                    padded = f" ❯ {user_input} ".ljust(width)
                    # Move cursor up to overwrite the raw input line
                    console.print(f"\033[1A\r[on grey15]{padded}[/on grey15]")

                    if is_slash_command(user_input):
                        ctx = {
                            "config": self.config,
                            "session": self.agent.session,
                            "context_manager": self.agent.session.context_manager,
                        }
                        result = await execute_slash_command(user_input, ctx)
                        if result == "__EXIT__":
                            break
                        if result and result.startswith("__RETRY__:"):
                            await self._handle_message(result[len("__RETRY__:"):])
                            continue
                        if result:
                            console.print(f"\n{result}")
                        continue

                    if user_input.startswith("!"):
                        cmd = user_input[1:].strip()
                        if cmd:
                            from permissions.bash_security import analyze_command
                            analysis = analyze_command(cmd)
                            if not analysis.is_safe:
                                console.print(f"[red]Blocked: {analysis.reason}[/red]")
                                continue
                            if analysis.is_destructive:
                                console.print(f"[yellow]Warning: {analysis.reason}[/yellow]")
                                try:
                                    confirm = input("  Proceed? (y/N) ").strip().lower()
                                    if confirm not in ("y", "yes"):
                                        continue
                                except (KeyboardInterrupt, EOFError):
                                    continue
                            # Filter secrets from environment
                            import fnmatch as _fnmatch
                            _secret_pats = ("*API_KEY*", "*SECRET*", "*TOKEN*", "*PASSWORD*", "*CREDENTIAL*", "ANTHROPIC_*", "OPENAI_*", "AZURE_*")
                            import os as _os
                            safe_env = {k: v for k, v in _os.environ.items() if not any(_fnmatch.fnmatch(k.upper(), p) for p in _secret_pats)}
                            proc = await asyncio.create_subprocess_shell(
                                cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.STDOUT,
                                env=safe_env,
                            )
                            out, _ = await proc.communicate()
                            console.print(out.decode("utf-8", errors="replace"), end="")
                        continue

                    await self._handle_message(user_input)

                except KeyboardInterrupt:
                    if self.agent:
                        self.agent.abort()
                    console.print("\n[dim]Interrupted. /exit to quit[/dim]")
                except EOFError:
                    break

        console.print("\n[dim]Goodbye![/dim]")

    async def _handle_message(self, message):
        streaming = False
        spinner = True
        self.tui.start_thinking_spinner()

        async for event in self.agent.run(message):
            if event.type == AgentEventType.TEXT_DELTA:
                if spinner:
                    self.tui.stop_thinking_spinner()
                    spinner = False
                if not streaming:
                    self.tui.begin_assistant()
                    streaming = True
                self.tui.stream_assistant_delta(event.data.get("content", ""))

            elif event.type == AgentEventType.TEXT_COMPLETE:
                if streaming:
                    self.tui.end_assistant()
                    streaming = False

            elif event.type == AgentEventType.AGENT_ERROR:
                if spinner:
                    self.tui.stop_thinking_spinner()
                    spinner = False
                if streaming:
                    self.tui.end_assistant()
                    streaming = False
                console.print(f"\n[red]Error: {event.data.get('error')}[/red]")

            elif event.type == AgentEventType.TOOL_CALL_START:
                if spinner:
                    self.tui.stop_thinking_spinner()
                    spinner = False
                self.tui.tool_call_start(
                    call_id=event.data.get("call_id"),
                    name=event.data.get("name", ""),
                    tool_kind=None,
                    arguments=event.data.get("arguments", {}),
                )

            elif event.type == AgentEventType.TOOL_CALL_COMPLETE:
                self.tui.tool_call_complete(
                    call_id=event.data.get("call_id"),
                    name=event.data.get("name", ""),
                    tool_kind=None,
                    success=event.data.get("success", False),
                    output=event.data.get("output", ""),
                    error=event.data.get("error"),
                    metadata=event.data.get("metadata", {}),
                    diff=event.data.get("diff"),
                    truncated=event.data.get("truncated", False),
                    exit_code=event.data.get("exit_code"),
                )

        if spinner:
            self.tui.stop_thinking_spinner()


@click.command()
@click.option("--cwd", "-c", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--model", "-m", type=str)
@click.option("--max-turns", type=int)
@click.option("--permission-mode", type=click.Choice(["default", "plan", "bypass", "accept_edits", "dont_ask"]))
@click.option("--debug", is_flag=True)
def main(cwd, model, max_turns, permission_mode, debug):
    config = load_config(cwd=cwd)
    if model:
        config.model_name = model
    if max_turns:
        config.max_turns = max_turns
    if permission_mode:
        config.permission_mode = permission_mode
    if debug:
        config.debug = True
        logging.basicConfig(level=logging.DEBUG)

    errors = config.validate()
    if errors:
        for e in errors:
            print(f"Error: {e}")
        sys.exit(1)

    asyncio.run(PieAgent(config).run())


if __name__ == "__main__":
    main()
