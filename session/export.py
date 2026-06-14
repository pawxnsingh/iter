"""
Session export — convert JSONL transcript to readable markdown.

Source: /export command
"""

from __future__ import annotations

from pathlib import Path

from session.storage import SessionStorage


def export_session_to_markdown(transcript_path: str | Path) -> str:
    """Export a session transcript to markdown format."""
    messages = SessionStorage.load_transcript(transcript_path)
    if not messages:
        return "# Empty Session\n\nNo messages in this transcript."

    lines = ["# Session Transcript", ""]

    first_ts = messages[0].get("timestamp", "")
    last_ts = messages[-1].get("timestamp", "")
    lines.append(f"**Started:** {first_ts}")
    lines.append(f"**Ended:** {last_ts}")
    lines.append(f"**Messages:** {len(messages)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")
        ts_short = timestamp[:19] if timestamp else ""

        if role == "user":
            lines.append(f"## User [{ts_short}]")
            lines.append("")
            lines.append(content)
            lines.append("")

        elif role == "assistant":
            lines.append(f"## Assistant [{ts_short}]")
            lines.append("")
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    lines.append(f"*Called tool: {name}*")
            if content:
                lines.append(content)
            lines.append("")

        elif role == "tool":
            tool_name = msg.get("tool_name", "tool")
            lines.append(f"### Tool Result: {tool_name}")
            lines.append("")
            if len(content) > 2000:
                lines.append(f"```\n{content[:2000]}\n... [truncated]\n```")
            else:
                lines.append(f"```\n{content}\n```")
            lines.append("")

    return "\n".join(lines)
