"""
EnterPlanMode tool — switches to plan mode (blocks write tools).

Source: src/tools/EnterPlanModeTool/

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

from pydantic import BaseModel

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.EnterPlanModeTool.prompt import ENTER_PLAN_MODE_TOOL_PROMPT


class EnterPlanParams(BaseModel):
    pass


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
        return ToolResult.success_result(
            "Entered plan mode. You should now focus on exploring the codebase "
            "and designing an implementation approach.\n\n"
            "In plan mode, you should:\n"
            "1. Thoroughly explore the codebase to understand existing patterns\n"
            "2. Identify similar features and architectural approaches\n"
            "3. Consider multiple approaches and their trade-offs\n"
            "4. Use AskUserQuestion if you need to clarify the approach\n"
            "5. Design a concrete implementation strategy\n"
            "6. When ready, use ExitPlanMode to present your plan for approval\n\n"
            "Remember: DO NOT write or edit any files yet. This is a read-only "
            "exploration and planning phase.",
            metadata={"set_permission_mode": "plan"},
        )
