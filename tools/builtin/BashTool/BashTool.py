import asyncio
import fnmatch
import os
import signal
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from tools.builtin.BashTool.prompt import BASH_PROMPT

# Output cap — CC parity (utils/shell/outputLimits.ts): default 30,000 chars,
# overridable via BASH_MAX_OUTPUT_LENGTH up to 150,000. Truncation keeps the
# HEAD of the output and reports how many lines were dropped.
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

# Commands that are always read-only (safe to run in parallel)
READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "head", "tail", "less", "more", "wc", "sort", "uniq",
    "grep", "rg", "find", "fd", "tree", "file", "stat", "du", "df",
    "which", "whereis", "type", "echo", "printf", "date", "whoami",
    "pwd", "env", "printenv", "hostname", "uname", "id",
    "git status", "git log", "git diff", "git show", "git blame",
    "git branch", "git remote", "git tag", "git stash list",
    "python --version", "python3 --version", "node --version",
    "npm --version", "pip --version", "cargo --version",
}

# Commands that are destructive (irreversible — extra warning)
DESTRUCTIVE_PATTERNS = {
    "rm -rf", "rm -r", "rm -f",
    "git push --force", "git push -f",
    "git reset --hard",
    "git clean -f", "git clean -fd",
    "git checkout .", "git restore .",
    "git stash drop", "git stash clear",
    "git branch -D",
    "drop table", "drop database", "truncate",
    "kubectl delete", "terraform destroy",
}

BLOCKED_COMMANDS = {
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /*",
    "dd if=/dev/zero",
    "dd if=/dev/random",
    "mkfs",
    "fdisk",
    "parted",
    ":(){ :|:& };:",  # Fork bomb
    "chmod 777 /",
    "chmod -R 777",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init 0",
    "init 6",
}


class ShellParams(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(
        120, ge=1, le=600, description="Timeout in seconds (default: 120)"
    )
    cwd: str | None = Field(None, description="Working directory for the command")


class BashTool(Tool):
    name = "Bash"
    kind = ToolKind.SHELL
    description = "Executes a given bash command and returns its output."
    prompt = BASH_PROMPT

    schema = ShellParams
    # CC parity: BashTool.tsx maxResultSizeChars: 30_000 (matches the 30k
    # output cap so the result-store limit and the inline cap agree).
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

    async def get_confirmation(
        self, invocation: ToolInvocation
    ) -> ToolConfirmation | None:
        params = ShellParams(**invocation.params)

        for blocked in BLOCKED_COMMANDS:
            if blocked in params.command:
                return ToolConfirmation(
                    tool_name=self.name,
                    params=invocation.params,
                    description=f"Execute (BLOCKED): {params.command}",
                    command=params.command,
                    is_dangerous=True,
                )

        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"Execute: {params.command}",
            command=params.command,
            is_dangerous=False,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ShellParams(**invocation.params)
        command = params.command.strip()

        # Use proper security analyzer (not substring matching)
        from permissions.bash_security import analyze_command
        analysis = analyze_command(command)
        if not analysis.is_safe:
            return ToolResult.error_result(
                f"Command blocked: {analysis.reason}",
                metadata={"blocked": True, "risk_level": analysis.risk_level},
            )

        # Also check the simple blocklist as a defense-in-depth layer
        for blocked in BLOCKED_COMMANDS:
            if blocked in command.lower():
                return ToolResult.error_result(
                    f"Command blocked for safety: {params.command}",
                    metadata={"blocked": True},
                )

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
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(),
                timeout=params.timeout,
            )
        except asyncio.TimeoutError:
            try:
                if sys.platform != "win32":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError):
                pass  # Process already exited
            await process.wait()
            return ToolResult.error_result(f"Command timed out after {params.timeout}s")

        stdout = stdout_data.decode("utf-8", errors="replace")
        stderr = stderr_data.decode("utf-8", errors="replace")
        exit_code = process.returncode

        output = ""
        if stdout.strip():
            output += stdout.rstrip()

        if stderr.strip():
            output += "\n--- stderr ---\n"
            output += stderr.rstrip()

        if exit_code != 0:
            output += f"\nExit code: {exit_code}"

        # Truncate keeping the head, reporting dropped line count (CC parity:
        # BashTool/utils.ts truncateOutput).
        max_len = _max_output_length()
        if len(output) > max_len:
            truncated_part = output[:max_len]
            remaining_lines = output.count("\n", max_len) + 1
            output = f"{truncated_part}\n\n... [{remaining_lines} lines truncated] ..."

        return ToolResult(
            success=exit_code == 0,
            output=output,
            error=stderr if exit_code != 0 else None,
            exit_code=exit_code,
        )

    def _build_environment(self) -> dict[str, str]:
        try:
            env = os.environ.copy()
            shell_environment = self.config.shell_environment

            # Default patterns + additional safety patterns
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
            # On any error, return minimal safe environment (platform-aware)
            safe_env = {"PATH": os.environ.get("PATH", os.defpath)}
            if sys.platform != "win32":
                safe_env["HOME"] = os.environ.get("HOME", "/tmp")
            else:
                safe_env["USERPROFILE"] = os.environ.get("USERPROFILE", "C:\\Users")
                safe_env["SystemRoot"] = os.environ.get("SystemRoot", "C:\\Windows")
            return safe_env
