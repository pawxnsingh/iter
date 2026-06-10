"""
ExitWorktree tool — exit a session-owned worktree.

Source: Claude Code src/tools/ExitWorktreeTool/

Operates only on worktrees created by EnterWorktree in the current session
(tracked in .ai-agent/worktree_session.json). Refuses to remove a worktree
with uncommitted changes or unmerged commits unless `discard_changes=true`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.EnterWorktreeTool.EnterWorktreeTool import (
    _run_git,
    clear_worktree_state,
    get_current_worktree_session,
)
from tools.builtin.ExitWorktreeTool.prompt import EXIT_WORKTREE_TOOL_PROMPT


class ExitWorktreeParams(BaseModel):
    action: Literal["keep", "remove"] = Field(
        ...,
        description='"keep" leaves the worktree and branch on disk; "remove" deletes both.',
    )
    discard_changes: bool = Field(
        False,
        description=(
            "Required true when action is 'remove' and the worktree has "
            "uncommitted files or unmerged commits. The tool will refuse and "
            "list them otherwise."
        ),
    )


class ExitWorktreeTool(Tool):
    name = "ExitWorktree"
    description = "Exit a worktree session created by EnterWorktree and return the session to the original working directory."
    prompt = EXIT_WORKTREE_TOOL_PROMPT
    kind = ToolKind.WRITE
    schema = ExitWorktreeParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ExitWorktreeParams(**invocation.params)
        cwd = invocation.cwd

        # Find session state. Look in cwd first (called from worktree), then
        # walk up. Simplest: try cwd, then parents up to filesystem root.
        session = None
        search = cwd
        while True:
            session = get_current_worktree_session(search)
            if session:
                state_root = search
                break
            if search.parent == search:
                break
            search = search.parent

        if not session:
            return ToolResult.success_result("No worktree session active. No-op.")

        worktree_path = session["worktree_path"]
        branch = session["branch"]
        original_cwd = session["original_cwd"]
        repo_root = session["repo_root"]

        if params.action == "remove":
            # Check for uncommitted changes
            code, status_out, _ = await _run_git(
                ["-C", worktree_path, "status", "--porcelain"],
                state_root,
            )
            uncommitted = [line for line in status_out.splitlines() if line.strip()]

            # Check for commits not on the original branch
            code2, rev_out, _ = await _run_git(
                ["-C", worktree_path, "rev-list", "--count", f"HEAD ^{repo_root}"],
                state_root,
            )
            # If git fails on rev-list (no upstream comparison possible), assume 0
            commits_ahead = 0
            try:
                commits_ahead = int(rev_out.strip().split()[0])
            except (ValueError, IndexError):
                commits_ahead = 0

            if (uncommitted or commits_ahead > 0) and not params.discard_changes:
                msg = "Refusing to remove worktree — has changes:"
                if uncommitted:
                    msg += f"\n  Uncommitted files: {len(uncommitted)}"
                if commits_ahead:
                    msg += f"\n  Commits ahead: {commits_ahead}"
                msg += "\nRe-invoke with discard_changes=true to force."
                return ToolResult.error_result(msg)

            # Remove the worktree
            code, _, err = await _run_git(
                ["worktree", "remove", "--force", worktree_path],
                repo_root,
            )
            if code != 0:
                return ToolResult.error_result(f"git worktree remove failed: {err.strip()}")

            # Delete the branch
            await _run_git(["branch", "-D", branch], repo_root)

            clear_worktree_state(state_root)
            return ToolResult.success_result(
                f"Worktree {worktree_path} removed (branch {branch} deleted). "
                f"Session restored to {original_cwd}.",
                metadata={
                    "action": "remove",
                    "original_cwd": original_cwd,
                    "worktree_path": worktree_path,
                    "worktree_branch": branch,
                },
            )

        # action == "keep"
        clear_worktree_state(state_root)
        return ToolResult.success_result(
            f"Left worktree {worktree_path} (branch {branch}) intact on disk. "
            f"Session restored to {original_cwd}.",
            metadata={
                "action": "keep",
                "original_cwd": original_cwd,
                "worktree_path": worktree_path,
                "worktree_branch": branch,
            },
        )
