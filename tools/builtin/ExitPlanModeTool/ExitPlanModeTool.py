"""
ExitPlanMode tool — exits plan mode (resumes normal execution).

Source: src/tools/ExitPlanModeTool/

Reads the most recent plan from .ai-agent/plans/ and signals the query loop
to switch permission_mode back to default via tool result metadata.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from core_types.permissions import PermissionMode
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.ExitPlanModeTool.prompt import EXIT_PLAN_MODE_TOOL_PROMPT


class ExitPlanParams(BaseModel):
    plan: str = Field(
        "",
        description="The implementation plan (markdown) to present to the user for approval.",
    )


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = "Exit plan mode after writing your plan. Signals that you're done planning and ready for the user to review and approve."
    prompt = EXIT_PLAN_MODE_TOOL_PROMPT
    kind = ToolKind.READ
    schema = ExitPlanParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return False

    def check_permissions(self, params: dict, mode) -> tuple[str, str] | None:
        return ("ask", "Exit plan mode?")

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ExitPlanParams(**invocation.params)

        if self.config.permission_mode != PermissionMode.PLAN:
            return ToolResult.error_result("You are not in plan mode.")

        plan = (params.plan or "").strip()
        plan_dir = invocation.cwd / ".ai-agent" / "plans"
        if not plan:
            plans = sorted(plan_dir.glob("plan_*.md"), reverse=True) if plan_dir.exists() else []
            for p in plans:
                c = p.read_text(encoding="utf-8").strip()
                if c and c != "# Implementation Plan":
                    plan = c
                    break
        if not plan:
            return ToolResult.error_result(
                "No plan provided. Pass the plan as the `plan` parameter."
            )

        plan_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_path = plan_dir / f"plan_{timestamp}.md"
        try:
            plan_path.write_text(plan, encoding="utf-8")
        except OSError:
            pass

        return ToolResult.success_result(
            "User has approved your plan. You can now start coding. Start with "
            "updating your todo list if applicable\n\n"
            f"Your plan has been saved to: {plan_path}\n"
            "You can refer back to it if needed during implementation.\n\n"
            f"## Approved Plan:\n{plan}",
            metadata={
                "plan_path": str(plan_path),
                "set_permission_mode": "default",
            },
        )
