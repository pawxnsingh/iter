import asyncio
import fnmatch
import os
import re
import signal
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.BashTool.prompt import BASH_PROMPT

BASH_MAX_OUTPUT_DEFAULT = 30_000
BASH_MAX_OUTPUT_UPPER_LIMIT = 150_000


def _max_output_length() -> int:
    raw = os.environ.get("BASH_MAX_OUTPUT_LENGTH")
    if raw:
        try:
            v = int(raw)
            if 1 <= v <= BASH_MAX_OUTPUT_UPPER_LIMIT:
                return v
        except ValueError:
            pass
    return BASH_MAX_OUTPUT_DEFAULT


_DEFAULT_TIMEOUT_MS = 120_000
_MAX_TIMEOUT_MS = 600_000


def _default_timeout_ms() -> int:
    """getDefaultBashTimeoutMs: BASH_DEFAULT_TIMEOUT_MS>0 else default."""
    raw = os.environ.get("BASH_DEFAULT_TIMEOUT_MS")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_MS

_GREP_LIKE = {"grep", "egrep", "fgrep", "rg", "ripgrep"}


def _format_duration(ms: int) -> str:
    """formatDuration: human-readable, not raw ms. For
    sub-minute integer-ms values the `ms < 1` branch is unreachable, so it
    floor-divides (e.g. 300ms → "0s", 2000ms → "2s")."""
    if ms < 60000:
        if ms == 0:
            return "0s"
        if ms < 1:
            return f"{ms / 1000:.1f}s"
        return f"{ms // 1000}s"
    days = ms // 86400000
    hours = (ms % 86400000) // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = round((ms % 60000) / 1000)
    if seconds == 60:
        seconds = 0; minutes += 1
    if minutes == 60:
        minutes = 0; hours += 1
    if hours == 24:
        hours = 0; days += 1
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) or "0s"


def _interpret_exit_code(command: str, exit_code: int) -> tuple[bool, str | None]:
    """interpretCommandResult — per-command exit-code semantics.

    grep/diff/find/test return 1 for an ordinary 'no result' condition, not an
    error. Returns (is_error, info_message). The base command is the first word
    of the LAST pipeline segment (that's what sets the exit code).
    """
    segments = re.split(r"\|\||&&|\||;", command)
    last = segments[-1].strip() if segments else command
    parts = last.split()
    base = parts[0].rsplit("/", 1)[-1] if parts else ""

    if base in _GREP_LIKE:
        return (exit_code >= 2, "No matches found" if exit_code == 1 else None)
    if base == "find":
        return (exit_code >= 2, "Some directories were inaccessible" if exit_code == 1 else None)
    if base == "diff":
        return (exit_code >= 2, "Files differ" if exit_code == 1 else None)
    if base in ("test", "["):
        return (exit_code >= 2, "Condition is false" if exit_code == 1 else None)
    return (exit_code != 0, None)


class ShellParams(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int | None = Field(
        None,
        ge=0,
        description=f"Optional timeout in milliseconds (max {_MAX_TIMEOUT_MS}).",
    )
    cwd: str | None = Field(None, description="Working directory for the command")


class BashTool(Tool):
    name = "Bash"
    kind = ToolKind.SHELL
    description = "Executes a given bash command and returns its output."
    prompt = BASH_PROMPT

    schema = ShellParams
    max_result_size_chars = 30_000

    def is_concurrency_safe(self, params: dict) -> bool:
        """Safe to run in parallel only if the command is read-only."""
        return self.is_read_only(params)

    def is_read_only(self, params: dict) -> bool:
        """Check if command is read-only using security analyzer."""
        from permissions.bash_security import analyze_command
        command = params.get("command", "").strip()
        if not command:
            return False
        analysis = analyze_command(command)
        return analysis.is_read_only

    def get_permission_value(self, params: dict) -> str | None:
        """Return the command string for rule sub-pattern matching."""
        cmd = params.get("command", "")
        return cmd.strip() if isinstance(cmd, str) else None

    def is_destructive(self, params: dict) -> bool:
        """Check if command is destructive using security analyzer."""
        from permissions.bash_security import analyze_command
        command = params.get("command", "").strip()
        if not command:
            return False
        analysis = analyze_command(command)
        return analysis.is_destructive

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ShellParams(**invocation.params)
        command = params.command.strip()


        if params.cwd:
            cwd = Path(params.cwd)
            if not cwd.is_absolute():
                cwd = invocation.cwd / cwd
        else:
            cwd = invocation.cwd

        if not cwd.exists():
            return ToolResult.error_result(f"Working directory doesn't exist: {cwd}")

        env = self._build_environment()
        if sys.platform == "win32":
            shell_cmd = ["cmd.exe", "/c", params.command]
        else:
            shell_cmd = ["/bin/bash", "-c", params.command]

        process = await asyncio.create_subprocess_exec(
            *shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )

        timeout_ms = params.timeout or _default_timeout_ms()
        abort = getattr(invocation, "abort_controller", None)

        def _kill() -> None:
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError):
                pass

        buf = bytearray()

        async def _drain() -> None:
            try:
                while True:
                    chunk = await process.stdout.read(8192)
                    if not chunk:
                        break
                    buf.extend(chunk)
            except Exception:
                pass

        drain_task = asyncio.ensure_future(_drain())
        wait_task = asyncio.ensure_future(process.wait())
        deadline = timeout_ms / 1000
        elapsed = 0.0
        poll = 0.1
        interrupted = False
        timed_out = False
        while True:
            done, _ = await asyncio.wait({wait_task}, timeout=poll)
            if wait_task in done:
                break
            if abort is not None and getattr(abort, "aborted", False):
                interrupted = True
                _kill()
                break
            elapsed += poll
            if elapsed >= deadline:
                timed_out = True
                _kill()
                break

        try:
            await asyncio.wait_for(drain_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            drain_task.cancel()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        stdout = bytes(buf).decode("utf-8", errors="replace")
        exit_code = process.returncode if process.returncode is not None else -1

        processed_stdout = re.sub(r"^(\s*\n)+", "", stdout).rstrip() if stdout else ""

        if interrupted:
            parts = [processed_stdout, "<error>Command was aborted before completion</error>"]
            return ToolResult(success=False, output="\n".join(p for p in parts if p),
                              exit_code=exit_code)
        if timed_out:
            parts = [processed_stdout,
                     f"<error>Command timed out after {_format_duration(timeout_ms)}</error>"]
            return ToolResult(success=False, output="\n".join(p for p in parts if p),
                              exit_code=exit_code)

        is_error, _semantic_msg = _interpret_exit_code(command, exit_code)
        output = processed_stdout
        if is_error:
            output += f"\nExit code {exit_code}"

        max_len = _max_output_length()
        if len(output) > max_len:
            truncated_part = output[:max_len]
            remaining_lines = output.count("\n", max_len) + 1
            output = f"{truncated_part}\n\n... [{remaining_lines} lines truncated] ..."

        return ToolResult(
            success=not is_error,
            output=output,
            error=None,
            exit_code=exit_code,
        )

    def _build_environment(self) -> dict[str, str]:
        try:
            env = os.environ.copy()
            shell_environment = self.config.shell_environment

            all_patterns = list(shell_environment.exclude_patterns) + [
                "*PASSWORD*", "*PASSWD*", "*CREDENTIAL*",
            ]

            if not shell_environment.ignore_default_excludes:
                for pattern in all_patterns:
                    keys_to_remove = [
                        k for k in env.keys() if fnmatch.fnmatch(k.upper(), pattern.upper())
                    ]
                    for k in keys_to_remove:
                        del env[k]

            if shell_environment.set_vars:
                env.update(shell_environment.set_vars)

            return env
        except Exception:
            safe_env = {"PATH": os.environ.get("PATH", os.defpath)}
            if sys.platform != "win32":
                safe_env["HOME"] = os.environ.get("HOME", "/tmp")
            else:
                safe_env["USERPROFILE"] = os.environ.get("USERPROFILE", "C:\\Users")
                safe_env["SystemRoot"] = os.environ.get("SystemRoot", "C:\\Windows")
            return safe_env
