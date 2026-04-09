"""
AskUser tool — ask the user questions mid-execution.

Source: Claude Code src/tools/AskUserQuestionTool/

Allows the agent to:
  1. Gather user preferences or requirements
  2. Clarify ambiguous instructions
  3. Get decisions on implementation choices
  4. Offer choices about direction to take

Users can always select "Other" for custom text input.
"""


from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult


class AskUserParams(BaseModel):
    question: str = Field(
        ...,
        description="The question to ask the user",
    )
    options: list[str] = Field(
        default_factory=list,
        description="Optional list of choices. Users can always type a custom answer.",
    )


class AskUserTool(Tool):
    name = "AskUserQuestion"
    description = (
        "Ask the user a question to gather preferences, clarify ambiguous instructions, "
        "get decisions on implementation choices, or offer choices. Users can always "
        "provide custom text input beyond the listed options."
    )
    kind = ToolKind.READ
    schema = AskUserParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False  # Blocks for user input

    def is_read_only(self, params: dict) -> bool:
        return True  # Doesn't modify anything

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = AskUserParams(**invocation.params)

        # Display question
        print()
        print(f"  ? {params.question}")

        # Display options if provided
        if params.options:
            for i, option in enumerate(params.options, 1):
                print(f"    {i}. {option}")
            print(f"    {len(params.options) + 1}. Other (type custom answer)")

        # Get user input
        try:
            answer = input("    > ").strip()
        except (KeyboardInterrupt, EOFError):
            return ToolResult.success_result("User declined to answer.")

        # If user typed a number, map to option
        if params.options and answer.isdigit():
            idx = int(answer) - 1
            if 0 <= idx < len(params.options):
                answer = params.options[idx]

        if not answer:
            return ToolResult.success_result("User provided no answer.")

        return ToolResult.success_result(
            f"User answered: {answer}",
            metadata={"answer": answer},
        )
