"""
EnterWorktree tool — create an isolated git worktree and switch into it.

Source: Claude Code src/tools/EnterWorktreeTool/

Creates a new git worktree inside .ai-agent/worktrees/, optionally with a
caller-provided name, and switches the session's working directory to it.
Session state for active worktrees is tracked at .ai-agent/worktree_session.json
so ExitWorktree (or session shutdown) can clean up.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.EnterWorktreeTool.prompt import ENTER_WORKTREE_TOOL_PROMPT

# Per-session worktree state file (one active worktree at a time)
_WORKTREE_STATE_REL = Path(".ai-agent") / "worktree_session.json"
_WORKTREES_DIR_REL = Path(".ai-agent") / "worktrees"

# Allowed worktree-slug shape (matches CC's validateWorktreeSlug)
_SLUG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")


def _state_path(cwd: Path) -> Path:
    return cwd / _WORKTREE_STATE_REL


def get_current_worktree_session(cwd: Path) -> dict | None:
    """Return the active worktree session record, or None."""
    p = _state_path(cwd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_worktree_state(cwd: Path, state: dict) -> None:
    p = _state_path(cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def clear_worktree_state(cwd: Path) -> None:
    p = _state_path(cwd)
    if p.exists():
        p.unlink()


def _validate_slug(name: str) -> None:
    if not _SLUG_RE.match(name):
        raise ValueError(
            "Worktree name may contain only letters, digits, dots, underscores, "
            "slashes, and dashes; max 64 chars total."
        )


def _generate_slug() -> str:
    """Random short slug, matches CC's getPlanSlug() shape (3-word style)."""
    return secrets.token_hex(4)


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git subcommand. Returns (exit_code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


class EnterWorktreeParams(BaseModel):
    name: str | None = Field(
        None,
        description=(
            'Optional name for the worktree. Each "/"-separated segment may '
            "contain only letters, digits, dots, underscores, and dashes; max "
            "64 chars total. A random name is generated if not provided."
        ),
    )


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = "Creates an isolated git worktree and switches the session into it"
    prompt = ENTER_WORKTREE_TOOL_PROMPT
    kind = ToolKind.WRITE
    schema = EnterWorktreeParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = EnterWorktreeParams(**invocation.params)
        cwd = invocation.cwd

        # Refuse if already in a session-owned worktree
        if get_current_worktree_session(cwd):
            return ToolResult.error_result("Already in a worktree session")

        # Validate slug, else generate one
        slug = params.name or _generate_slug()
        try:
            _validate_slug(slug)
        except ValueError as e:
            return ToolResult.error_result(str(e))

        # Confirm we're in a git repo
        code, root_out, _ = await _run_git(["rev-parse", "--show-toplevel"], cwd)
        if code != 0:
            return ToolResult.error_result("Not inside a git repository")
        repo_root = Path(root_out.strip())

        # Resolve target worktree path under repo_root/.ai-agent/worktrees/<slug>
        worktree_path = repo_root / _WORKTREES_DIR_REL / slug
        if worktree_path.exists():
            return ToolResult.error_result(f"Worktree path already exists: {worktree_path}")

        # Branch name (prefix to avoid clobbering existing branches)
        branch = f"ai-agent-worktree/{slug}"
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Create the worktree on a new branch from HEAD
        code, _, err = await _run_git(
            ["worktree", "add", "-b", branch, str(worktree_path), "HEAD"], repo_root,
        )
        if code != 0:
            return ToolResult.error_result(f"git worktree add failed: {err.strip()}")

        # Persist session state (under repo_root, not the new worktree)
        state = {
            "slug": slug,
            "branch": branch,
            "worktree_path": str(worktree_path),
            "original_cwd": str(cwd),
            "repo_root": str(repo_root),
        }
        save_worktree_state(repo_root, state)

        return ToolResult.success_result(
            f"Worktree created at {worktree_path} on branch {branch}.\n"
            f"Subsequent file operations should use this path. "
            f"Call ExitWorktree to clean up.",
            metadata={
                "worktree_path": str(worktree_path),
                "worktree_branch": branch,
            },
        )
