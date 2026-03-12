"""
Collapsible tool-call card.

Pattern (from toad/widgets/tool_call.py):
  - A VerticalGroup holding a clickable header + hidden content body.
  - Reactives with `toggle_class=` drive CSS state:
      expanded   → shows the body, unfolds header text
      has_content → enables the expansion affordance
      running    → header pulses
      success/error → status pill on the right
  - Content is chosen based on tool output: diff for Edit/Write, syntax-
    highlighted code for Bash stderr, Markdown if fenced, else plain Static.

We keep the surface minimal compared to toad (no BlockProtocol / menus yet)
but preserve the expand/collapse UX and status semantics.
"""

from __future__ import annotations

import re
from typing import Any

from textual import events, on
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.content import Content
from textual.reactive import var
from textual.widgets import Markdown, Static

from ui.widgets.pill import pill


_ANSI = "\x1b"
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s.*$", re.MULTILINE)


# Short, human-readable verb per tool name. Unknown tools fall back to the name itself.
_VERB = {
    "Read": "Reading",
    "Edit": "Edit",
    "Write": "Write",
    "Bash": "Bash",
    "Grep": "Search",
    "Glob": "Glob",
    "ListDir": "List",
    "WebSearch": "WebSearch",
    "WebFetch": "WebFetch",
}


def _summary(name: str, args: dict[str, Any]) -> str:
    """One-liner describing what the tool is doing."""
    match name:
        case "Read" | "Edit" | "Write":
            return str(args.get("path", ""))
        case "Bash":
            return (args.get("description") or args.get("command") or "")[:80]
        case "Grep":
            return f'"{args.get("pattern", "")}"'
        case "Glob":
            return f'"{args.get("pattern", "")}"'
        case "ListDir":
            return str(args.get("path", "."))
        case "WebSearch":
            return str(args.get("query", ""))
        case "WebFetch":
            return str(args.get("url", ""))[:60]
        case _:
            # Show first string arg we find
            for v in args.values():
                if isinstance(v, str) and v:
                    return v[:80]
            return ""


def _looks_like_markdown(text: str) -> bool:
    return "```" in text or bool(_MARKDOWN_HEADING.search(text))


class ToolCallHeader(Static):
    """Clickable one-liner. Wraps when expanded."""

    DEFAULT_CSS = """
    ToolCallHeader {
        pointer: pointer;
        color: $text-secondary;
        width: 1fr;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """


class ToolCall(VerticalGroup):
    """Tool invocation card — header + (optional) collapsible body."""

    DEFAULT_CLASSES = "block"
    DEFAULT_CSS = """
    ToolCall {
        width: 1fr;
        height: auto;
        padding: 0;
        margin: 0 1 0 1;

        #tool-content {
            display: none;
            margin: 1 0 0 2;
            padding: 0 1 0 1;
            border-left: blank $primary 40%;
        }
        &.-expanded #tool-content { display: block; }
        &.-expanded ToolCallHeader {
            text-wrap: wrap;
            text-overflow: fold;
        }

        &.-running ToolCallHeader { color: $text-accent; }
        &.-success ToolCallHeader { color: $text-success; }
        &.-error   ToolCallHeader { color: $text-error;   }

        #tool-content > Markdown,
        #tool-content > Static {
            padding: 0;
            margin: 0;
        }
    }
    """

    # Public reactives drive CSS class state. Tool status (running/success/
    # error) is a tri-state toggled via plain add_class/remove_class to
    # avoid underscore-prefixed reactive names, which conflict with Textual
    # internals like MessagePump._is_mounted during early mount.
    has_content: var[bool] = var(False, toggle_class="-has-content")
    expanded: var[bool] = var(False, toggle_class="-expanded")

    STATUS_CLASSES = ("-running", "-success", "-error")

    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.call_id = call_id
        self.tool_name = name
        self.arguments = arguments
        self._status: str = "running"  # "running" | "success" | "error"
        self.add_class("-running")

    @property
    def is_running(self) -> bool:
        return self._status == "running"

    @property
    def is_success(self) -> bool:
        return self._status == "success"

    @property
    def is_error(self) -> bool:
        return self._status == "error"

    def _set_status(self, status: str) -> None:
        self._status = status
        self.remove_class(*self.STATUS_CLASSES, update=False)
        self.add_class(f"-{status}")
        self._refresh_header()

    def compose(self) -> ComposeResult:
        yield ToolCallHeader(self._render_header())
        yield VerticalGroup(id="tool-content")

    # ─── Header ───────────────────────────────────────────────────────

    def _render_header(self) -> Content:
        verb = _VERB.get(self.tool_name, self.tool_name)
        summary = _summary(self.tool_name, self.arguments)
        icon = "▸" if self.has_content and not self.expanded else (
            "▾" if self.has_content and self.expanded else "●"
        )

        parts: list = [
            (f" {icon} ", "$text-muted" if not self.is_running else "$text-accent"),
            (verb, "bold"),
        ]
        if summary:
            parts.append("  ")
            parts.append((summary, "$text-muted"))

        if self.is_success:
            parts.append("   ")
            parts.append(("✔ done", "$text-success"))
        elif self.is_error:
            parts.append("   ")
            parts.append(pill(" failed ", "$error", "$text"))

        return Content.assemble(*parts)

    def _refresh_header(self) -> None:
        try:
            header = self.query_one(ToolCallHeader)
        except Exception:
            return
        header.update(self._render_header())

    # ─── Completion ───────────────────────────────────────────────────

    async def complete(
        self,
        *,
        success: bool,
        output: str,
        error: str | None,
        diff: str | None,
        metadata: dict[str, Any],
        exit_code: int | None,
        truncated: bool,
    ) -> None:
        """Transition from running → finished, populate body if interesting."""
        self._set_status("success" if success else "error")

        body = self.query_one("#tool-content", VerticalGroup)

        rendered_any = False
        if not success and error:
            await body.mount(Static(f"[b $text-error]error:[/] {error}"))
            rendered_any = True
        if diff and self.tool_name in ("Edit", "Write"):
            await body.mount(Markdown(f"```diff\n{diff.strip()}\n```"))
            rendered_any = True
        elif output:
            stripped = output.strip()
            if stripped:
                if _ANSI in stripped:
                    # Don't parse ANSI in a basic port; strip codes and show plain
                    clean = re.sub(r"\x1b\[[0-9;]*m", "", stripped)
                    await body.mount(Static(clean))
                elif _looks_like_markdown(stripped):
                    await body.mount(Markdown(stripped))
                else:
                    # Cap plain output at ~40 lines for collapsed-first UX
                    lines = stripped.split("\n")
                    capped = "\n".join(lines[:40])
                    if len(lines) > 40:
                        capped += f"\n… ({len(lines) - 40} more lines)"
                    await body.mount(Static(capped))
                rendered_any = True

        if truncated:
            await body.mount(Static("… truncated", classes="muted"))
            rendered_any = True

        self.has_content = rendered_any
        # Auto-expand on error so the user sees what went wrong
        if self.is_error and rendered_any:
            self.expanded = True

        self._refresh_header()

    # ─── Interactions ─────────────────────────────────────────────────

    @on(events.Click)
    def _on_click(self, event: events.Click) -> None:
        if self.has_content:
            self.expanded = not self.expanded

    def watch_expanded(self, _: bool) -> None:
        self._refresh_header()

    def watch_has_content(self, _: bool) -> None:
        self._refresh_header()
