"""
AskUser tool — ask the user questions mid-execution.

Verbatim schema parity:
  Input: questions: list[Question], min 1 max 4
    Question:
      question: str        (full question, ends with ?)
      header:   str        (chip label, max 12 chars)
      options:  list[Option], min 2 max 4
        Option:
          label:       str (1-5 words)
          description: str (explanation of trade-offs)
          preview:     str | None (optional code/mockup)
      multiSelect: bool    (default False)
    annotations: dict[str, {preview?, notes?}] | None
    answers:     dict[str, str] | None
    metadata:    {source?: str} | None

  Refinement: question texts unique; option labels unique within each question.

  Output: {questions, answers (question_text → answer_str), annotations?}
  Multi-select answers comma-separated, mirroring the output contract.
"""


from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.AskUserQuestionTool.prompt import (
    ASK_USER_QUESTION_TOOL_CHIP_WIDTH,
    ASK_USER_QUESTION_TOOL_PROMPT,
    DESCRIPTION,
)


class QuestionOption(BaseModel):
    label: str = Field(
        ...,
        description=(
            "The display text for this option that the user will see and "
            "select. Should be concise (1-5 words) and clearly describe the "
            "choice."
        ),
    )
    description: str = Field(
        ...,
        description=(
            "Explanation of what this option means or what will happen if "
            "chosen. Useful for providing context about trade-offs or "
            "implications."
        ),
    )
    preview: str | None = Field(
        default=None,
        description=(
            "Optional preview content rendered when this option is focused. "
            "Use for mockups, code snippets, or visual comparisons that help "
            "users compare options. See the tool description for the "
            "expected content format."
        ),
    )


class Question(BaseModel):
    question: str = Field(
        ...,
        description=(
            'The complete question to ask the user. Should be clear, '
            'specific, and end with a question mark. Example: "Which library '
            'should we use for date formatting?" If multiSelect is true, '
            'phrase it accordingly, e.g. "Which features do you want to '
            'enable?"'
        ),
    )
    header: str = Field(
        ...,
        description=(
            f"Very short label displayed as a chip/tag (max "
            f"{ASK_USER_QUESTION_TOOL_CHIP_WIDTH} chars). Examples: "
            f'"Auth method", "Library", "Approach".'
        ),
    )
    options: list[QuestionOption] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "The available choices for this question. Must have 2-4 "
            "options. Each option should be a distinct, mutually exclusive "
            "choice (unless multiSelect is enabled). There should be no "
            "'Other' option, that will be provided automatically."
        ),
    )
    multiSelect: bool = Field(
        default=False,
        description=(
            "Set to true to allow the user to select multiple options "
            "instead of just one. Use when choices are not mutually "
            "exclusive."
        ),
    )


class QuestionAnnotation(BaseModel):
    preview: str | None = Field(
        default=None,
        description=(
            "The preview content of the selected option, if the question "
            "used previews."
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Free-text notes the user added to their selection.",
    )


class AskUserParams(BaseModel):
    questions: list[Question] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="Questions to ask the user (1-4 questions)",
    )
    annotations: dict[str, QuestionAnnotation] | None = Field(
        default=None,
        description=(
            "Optional per-question annotations from the user (e.g., notes "
            "on preview selections). Keyed by question text."
        ),
    )
    answers: dict[str, str] | None = Field(
        default=None,
        description="User answers collected by the permission component",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata for tracking and analytics purposes. Not "
            "displayed to user."
        ),
    )

    @model_validator(mode="after")
    def _check_uniqueness(self) -> "AskUserParams":
        seen: set[str] = set()
        for q in self.questions:
            if q.question in seen:
                raise ValueError(
                    "Question texts must be unique, option labels must be "
                    "unique within each question"
                )
            seen.add(q.question)
            labels = [opt.label for opt in q.options]
            if len(labels) != len(set(labels)):
                raise ValueError(
                    "Question texts must be unique, option labels must be "
                    "unique within each question"
                )
        return self


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = DESCRIPTION
    prompt = ASK_USER_QUESTION_TOOL_PROMPT
    kind = ToolKind.READ
    schema = AskUserParams

    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    def is_read_only(self, params: dict) -> bool:
        return True

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = AskUserParams(**invocation.params)

        answers: dict[str, str] = {}

        for q in params.questions:
            print()
            chip = f"[{q.header[:ASK_USER_QUESTION_TOOL_CHIP_WIDTH]}]"
            print(f"  ? {chip} {q.question}")
            for i, opt in enumerate(q.options, 1):
                print(f"    {i}. {opt.label} — {opt.description}")
                if opt.preview:
                    preview = opt.preview.replace("\n", "\n       ")
                    print(f"       {preview}")
            print(f"    {len(q.options) + 1}. Other (type custom answer)")

            mode_hint = (
                " (comma-separated for multi-select)" if q.multiSelect else ""
            )
            try:
                raw = input(f"    >{mode_hint} ").strip()
            except (KeyboardInterrupt, EOFError):
                answers[q.question] = ""
                continue

            if not raw:
                answers[q.question] = ""
                continue

            picks: list[str] = []
            for tok in (s.strip() for s in raw.split(",")):
                if not tok:
                    continue
                if tok.isdigit():
                    idx = int(tok) - 1
                    if 0 <= idx < len(q.options):
                        picks.append(q.options[idx].label)
                        continue
                picks.append(tok)

            if not q.multiSelect and picks:
                picks = picks[:1]
            answers[q.question] = ", ".join(picks)

        parts: list[str] = []
        for q in params.questions:
            ans = answers.get(q.question, "")
            seg = [f'"{q.question}"="{ans}"']
            ann = (params.annotations or {}).get(q.question)
            if ann and ann.preview:
                seg.append(f"selected preview:\n{ann.preview}")
            if ann and ann.notes:
                seg.append(f"user notes: {ann.notes}")
            parts.append(" ".join(seg))
        answers_text = ", ".join(parts)

        return ToolResult.success_result(
            output=(
                f"User has answered your questions: {answers_text}. "
                "You can now continue with the user's answers in mind."
            ),
            metadata={
                "questions": [q.model_dump() for q in params.questions],
                "answers": answers,
            },
        )
