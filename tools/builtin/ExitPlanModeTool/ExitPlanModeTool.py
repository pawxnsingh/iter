"""
ExitPlanMode tool — exits plan mode (resumes normal execution).

Source: Claude Code src/tools/ExitPlanModeTool/

Reads the most recent plan from .ai-agent/plans/ and signals the query loop
to switch permission_mode back to default via tool result metadata.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.ExitPlanModeTool.prompt import EXIT_PLAN_MODE_TOOL_PROMPT


class ExitPlanParams(BaseModel):
    pass  # No parameters needed


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = "Exit plan mode after writing your plan. Signals that you're done planning and ready for the user to review and approve."
    prompt = EXIT_PLAN_MODE_TOOL_PROMPT
    kind = ToolKind.READ
    schema = ExitPlanParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        plan_dir = invocation.cwd / ".ai-agent" / "plans"
        if not plan_dir.exists():
            return ToolResult.error_result("No plan file found. Use EnterPlanMode first.")

        plans = sorted(plan_dir.glob("plan_*.md"), reverse=True)
        if not plans:
            return ToolResult.error_result("No plan file found.")

        plan_path = plans[0]
        plan_content = plan_path.read_text(encoding="utf-8")

        if not plan_content.strip() or plan_content.strip() == "# Implementation Plan":
            return ToolResult.error_result(
                "Plan file is empty. Write your plan before calling ExitPlanMode."
            )

        return ToolResult.success_result(
            f"Plan ready for review ({plan_path.name}).\n"
            f"Exiting plan mode — tools will execute normally.",
            metadata={
                "plan_path": str(plan_path),
                "set_permission_mode": "default",
            },
        )
