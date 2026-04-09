"""
PieAgent TUI — Rich-based, matching Claude Code visual patterns.

Rendering approach:
  - Tool running: use Rich Live to show animated spinner (replaced on complete)
  - Tool complete: ● green/red dot + bold name + details + collapsed summary
  - Assistant text: rendered as Markdown (bold, tables, lists, code)
  - No duplicate lines (running line gets replaced by complete line via Live)
"""

import random
import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from config.config import Config

BLACK_CIRCLE = "●"
PROMPT_CHAR = "❯"
CORNER = "⎿"
INDENT = "     "  # 5 chars to align with text after "  ⎿  "

SPINNER_VERBS = [
    "Thinking",
    "Reasoning",
    "Analyzing",
    "Processing",
    "Working",
    "Crafting",
    "Evaluating",
    "Considering",
]

AGENT_THEME = Theme(
    {
        "success": "green",
        "error": "bright_red",
        "warning": "yellow",
        "dim": "dim",
        "muted": "grey50",
        "claude": "rgb(215,119,87)",
    }
)

_console: Console | None = None


def get_console() -> Console:
    global _console
    if _console is None:
        _console = Console(theme=AGENT_THEME, highlight=False)
    return _console


def _dp(path: str, cwd: Path) -> str:
    try:
        return str(Path(path).relative_to(cwd))
    except (ValueError, TypeError):
        return path or ""


class TUI:
    def __init__(self, console: Console | None, config: Config) -> None:
        self.console = console or get_console()
        self._streaming = False
        self._stream_buffer = ""
        self.config = config
        self.cwd = self.config.cwd
        self._tool_args: dict[str, dict[str, Any]] = {}
        self._tool_live: Live | None = None  # For replacing running→complete
        self._thinking_live: Live | None = None

    # ── Thinking spinner ─────────────────────────────────────────

    def start_thinking_spinner(self):
        verb = random.choice(SPINNER_VERBS)
        # Claude Code spinner characters: · ✢ ✳ ✶ ✻ ✽ (forward then reverse)
        # We register a custom spinner matching Claude Code's SpinnerGlyph
        from rich.spinner import SPINNERS

        SPINNERS["claude"] = {
            "interval": 120,
            "frames": ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"],
        }
        self._thinking_live = Live(
            Spinner("claude", text=f" {verb}...", style="claude"),
            console=self.console,
            transient=True,
            refresh_per_second=10,
        )
        try:
            self._thinking_live.start()
        except Exception:
            self._thinking_live = None

    def stop_thinking_spinner(self):
        if self._thinking_live:
            try:
                self._thinking_live.stop()
            except Exception:
                pass
            self._thinking_live = None

    # ── Welcome ──────────────────────────────────────────────────

    def print_welcome(self, title: str, lines: list[str]):
        """PieAgent branded header with mascot."""

        model_info = lines[0] if len(lines) > 0 else ""
        cwd_info = lines[1] if len(lines) > 1 else ""

        self.console.print()
        self.console.print(Text.assemble(("   ",""), ("▄▄▄▄▄▄▄▄","claude"),("   ",""),  (f"{title}","bold claude"), (" v0.1.0","dim")))
        self.console.print(f"   [claude]██▀▀▀▀██[/claude]   [dim]{model_info}[/dim]")
        self.console.print(f"   [claude]██▄▄▄▄██[/claude]   [dim]{cwd_info}[/dim]")
        self.console.print("   [claude]▀▀▀▀▀▀▀▀[/claude]   [dim]? for shortcuts · /help for commands[/dim]")
        self.console.print()

    # ── Assistant text ────────────────────────────────────────────

    def begin_assistant(self):
        self.console.print()
        self._streaming = True
        self._stream_buffer = ""

    def end_assistant(self):
        if self._streaming and self._stream_buffer:
            self._render_assistant(self._stream_buffer)
        self._streaming = False
        self._stream_buffer = ""

    def stream_assistant_delta(self, content: str) -> None:
        if self._streaming:
            self._stream_buffer += content

    def _render_assistant(self, text: str) -> None:
        """Render assistant response as Markdown with ⎿ ● prefix."""
        # Print the ⎿ ● prefix on its own line
        self.console.print(
            Text.assemble(
                (f"  {CORNER}  ", "dim"),
                (f"{BLACK_CIRCLE} ", "success"),
            ),
            end="",
        )

        # Check if text has code blocks — split and render separately
        code_pattern = re.compile(r"```(\w*)\n([\s\S]*?)```")

        if code_pattern.search(text):
            # Has code blocks — render in parts
            last_end = 0
            first = True
            for match in code_pattern.finditer(text):
                # Text before code block
                before = text[last_end : match.start()].strip()
                if before:
                    if first:
                        # First text chunk goes on same line as ●
                        self.console.print(Markdown(before))
                        first = False
                    else:
                        self.console.print(Markdown(before))

                # Code block — render as syntax
                lang = match.group(1) or "text"
                code = match.group(2).strip()
                if code:
                    self.console.print(
                        Syntax(code, lang, theme="monokai", padding=(0, 0, 0, 4))
                    )

                last_end = match.end()

            # Text after last code block
            after = text[last_end:].strip()
            if after:
                self.console.print(Markdown(after))
        else:
            # No code blocks — render entire text as Markdown
            self.console.print(Markdown(text))

    # ── Tool call start ──────────────────────────────────────────
    # Use Rich Live so the running line gets REPLACED by the complete line

    def tool_call_start(
        self, call_id: str, name: str, tool_kind: str | None, arguments: dict[str, Any]
    ):
        self._tool_args[call_id] = arguments
        ufn = self._ufn(name, arguments)
        details = self._details(name, arguments)
        desc = f"{ufn}({details})" if details else ufn

        # Start a Live display that will be replaced by tool_call_complete
        running_text = Text.assemble(
            (f"  {CORNER}  ", "dim"),
            (f"{BLACK_CIRCLE} ", "dim"),
            (desc, "dim"),
        )
        self._tool_live = Live(running_text, console=self.console, transient=True)
        try:
            self._tool_live.start()
        except Exception:
            self._tool_live = None
            # Fallback: just print dim line
            self.console.print(running_text)

    # ── Tool call complete ───────────────────────────────────────
    # Replace the running line with the completed line

    def tool_call_complete(
        self,
        call_id: str,
        name: str,
        tool_kind: str | None,
        success: bool,
        output: str,
        error: str | None,
        metadata: dict[str, Any],
        diff: str | None,
        truncated: bool,
        exit_code: int | None,
    ):
        # Stop the running Live display
        if self._tool_live:
            try:
                self._tool_live.stop()
            except Exception:
                pass
            self._tool_live = None

        args = self._tool_args.get(call_id, {})
        dot = "success" if success else "error"
        ufn = self._ufn(name, args)
        details = self._details(name, args)

        # ⎿  ● ToolName(details) — single completed line
        parts = [
            (f"  {CORNER}  ", "dim"),
            (f"{BLACK_CIRCLE} ", dot),
            (ufn, "bold"),
        ]
        if details:
            parts.append((f"({details})", ""))
        self.console.print(Text.assemble(*parts))

        # Collapsed summary (Claude Code hides output by default)
        # Use Text() for error/summary to prevent Rich markup injection
        from rich.markup import escape
        if not success and error:
            self.console.print(Text(f"{INDENT}  {escape(error)}", style="error"))
        elif diff and name in ("Edit", "Write"):
            self._render_code(diff, "diff")
        elif name == "Bash" and exit_code and exit_code != 0:
            self._render_code(output, "text")
        else:
            summary = self._summary(name, metadata, output)
            if summary:
                self.console.print(Text(f"{INDENT}  {summary}", style="dim"))

        if truncated:
            self.console.print(f"{INDENT}  [warning]… truncated[/warning]")

    # ── Permission dialog ────────────────────────────────────────

    def show_permission_dialog(
        self, tool_name: str, description: str, is_destructive: bool = False
    ) -> bool:
        self.console.print()
        style = "bold error" if is_destructive else "bold"
        self.console.print(f"  [{style}]{tool_name}[/{style}]")
        self.console.print(f"{INDENT}  [dim]{description}[/dim]")
        self.console.print()
        self.console.print("  [bold]This command requires approval[/bold]")
        self.console.print("  Do you want to proceed?")
        self.console.print(f"  [bold]{PROMPT_CHAR}[/bold] 1. Yes")
        self.console.print("    2. Yes, and don't ask again")
        self.console.print("    3. No")
        self.console.print()
        self.console.print("  [dim]Esc to cancel · Tab to amend[/dim]")
        try:
            return self.console.input("  ").strip() in ("1", "y", "yes", "2")
        except (KeyboardInterrupt, EOFError):
            return False

    # ── Helpers ───────────────────────────────────────────────────

    def _ufn(self, name: str, args: dict[str, Any]) -> str:
        return {
            "Read": "Reading",
            "Edit": "Update",
            "Write": "Write",
            "Bash": "Bash",
            "Grep": "Search",
            "Glob": "Search",
            "ListDir": "List",
            "WebSearch": "WebSearch",
            "WebFetch": "WebFetch",
        }.get(name, name)

    def _details(self, name: str, args: dict[str, Any]) -> str:
        match name:
            case "Read" | "Edit" | "Write":
                return _dp(args.get("path", ""), self.cwd)
            case "Bash":
                d = args.get("description", "")
                if d:
                    return d[:80]
                c = args.get("command", "")
                return c[:80] + ("…" if len(c) > 80 else "")
            case "Grep":
                p = [f'pattern: "{args.get("pattern", "")}"']
                path = args.get("path")
                if path and path != ".":
                    p.append(f'path: "{_dp(path, self.cwd)}"')
                return ", ".join(p)
            case "Glob":
                return f'pattern: "{args.get("pattern", "")}"'
            case "ListDir":
                return _dp(args.get("path", "."), self.cwd)
            case "WebSearch":
                return args.get("query", "")
            case "WebFetch":
                return args.get("url", "")[:60]
            case _:
                return ""

    def _summary(self, name: str, meta: dict[str, Any], output: str) -> str:
        match name:
            case "Read":
                t = meta.get("total_lines")
                return (
                    f"Read {_dp(meta.get('path', ''), self.cwd)} ({t} lines)"
                    if t
                    else ""
                )
            case "ListDir":
                return f"Listed {meta.get('entries', 0)} items"
            case "Grep":
                return f"Found {meta.get('matches', 0)} results"
            case "Glob":
                return f"Found {meta.get('matches', 0)} files"
            case "Bash":
                n = len(output.strip().split("\n")) if output.strip() else 0
                return f"Ran successfully ({n} lines)" if n else "Ran successfully"
            case "WebSearch":
                return f"Found {meta.get('results', 0)} results"
            case "WebFetch":
                s = meta.get("content_length", 0)
                return f"Fetched {s:,} bytes" if s else "Fetched"
            case _:
                return ""

    def _render_code(self, output: str, lang: str):
        lines = output.strip().split("\n")
        if len(lines) > 20:
            lines = lines[:20] + [f"… [{len(lines)} total lines]"]
        self.console.print(
            Syntax("\n".join(lines), lang, theme="monokai", padding=(0, 0, 0, 6))
        )

