"""
Plan mode tools — EnterPlanMode and ExitPlanMode.

Source: Claude Code src/tools/EnterPlanModeTool/, src/tools/ExitPlanModeTool/

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


class EnterPlanParams(BaseModel):
    pass  # No parameters needed


class ExitPlanParams(BaseModel):
    pass  # No parameters needed


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = (
        "Enter plan mode to design an implementation approach before writing code. "
        "In plan mode, you can explore the codebase with read-only tools (Read, Grep, Glob) "
        "but cannot modify files. Write your plan, then use ExitPlanMode when ready for "
        "user approval. Use this proactively for non-trivial implementation tasks."
    )
    kind = ToolKind.READ
    schema = EnterPlanParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True  # Plan mode itself is a read-only transition

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        # Create plan file
        plan_dir = invocation.cwd / ".ai-agent" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_path = plan_dir / f"plan_{timestamp}.md"
        plan_path.write_text("# Implementation Plan\n\n", encoding="utf-8")

        # Set permission mode to plan (blocks writes)
        # This is communicated back via metadata — the query loop reads it
        return ToolResult.success_result(
            f"Entered plan mode. Write your plan to: {plan_path}\n"
            f"Use read-only tools (Read, Grep, Glob) to explore.\n"
            f"Call ExitPlanMode when your plan is ready for review.",
            metadata={
                "plan_path": str(plan_path),
                "set_permission_mode": "plan",
            },
        )


class ExitPlanModeTool(Tool):
    name = "ExitPlanMode"
    description = (
        "Exit plan mode after writing your plan. The user will review and approve "
        "the plan before implementation begins. You should have already written your "
        "plan to the plan file. This tool reads the plan from the file and presents "
        "it for approval."
    )
    kind = ToolKind.READ
    schema = ExitPlanParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        # Find the most recent plan file
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
