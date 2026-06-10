"""
EnterPlanMode tool — switches to plan mode (blocks write tools).

Source: Claude Code src/tools/EnterPlanModeTool/

Plan mode transitions:
  EnterPlanMode: switches to plan mode (blocks write tools)
    → Model explores codebase with read-only tools
    → Model writes plan to a plan file
  ExitPlanMode: exits plan mode (resumes normal execution)
    → User reviews and approves the plan
    → Model implements the plan

The plan file is written to .ai-agent/plans/<timestamp>.md
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.EnterPlanModeTool.prompt import ENTER_PLAN_MODE_TOOL_PROMPT


class EnterPlanParams(BaseModel):
    pass  # No parameters needed


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = "Transitions you into plan mode where you can explore the codebase and design an implementation approach for user approval."
    prompt = ENTER_PLAN_MODE_TOOL_PROMPT
    kind = ToolKind.READ
    schema = EnterPlanParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        plan_dir = invocation.cwd / ".ai-agent" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_path = plan_dir / f"plan_{timestamp}.md"
        plan_path.write_text("# Implementation Plan\n\n", encoding="utf-8")

        return ToolResult.success_result(
            f"Entered plan mode. Write your plan to: {plan_path}\n"
            f"Use read-only tools (Read, Grep, Glob) to explore.\n"
            f"Call ExitPlanMode when your plan is ready for review.",
            metadata={
                "plan_path": str(plan_path),
                "set_permission_mode": "plan",
            },
        )
