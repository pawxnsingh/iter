from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import model_json_schema

from config.config import Config


class ToolKind(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"

    MEMORY = "memory"
    NETWORK = "network"
    MCP = "mcp"


@dataclass
class FileDiff:
    path: Path
    old_content: str
    new_content: str

    is_new_file: bool = False
    is_deletion: bool = False

    def to_diff(self) -> str:
        import difflib

        old_lines = self.old_content.splitlines(keepends=True)
        new_lines = self.new_content.splitlines(keepends=True)

        if old_lines and not old_lines[-1].endswith("\n"):
            old_lines[-1] += "\n"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        old_name = "/dev/null" if self.is_new_file else str(self.path)
        new_name = "/dev/null" if self.is_deletion else str(self.path)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_name,
            tofile=new_name,
        )

        return "".join(diff)


@dataclass
class ToolInvocation:
    params: dict[str, Any]
    cwd: Path


@dataclass
class ToolConfirmation:
    tool_name: str
    params: dict[str, Any]
    description: str

    # shell commands
    command: str | None = None
    is_dangerous: bool = False


@dataclass
class ToolResult:
    success: bool
    output: str
    diff: dict[str, Any] | None = None
    truncated: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    # Image attachments for multimodal tool results (CC: FileRead returns an
    # image block for image files). Each entry: {"media_type": str, "data":
    # base64-str}. The query loop turns these into provider image blocks.
    images: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def error_result(
        cls,
        error: str,
        output: str = "",
        **kwargs: Any,
    ):
        return cls(
            success=False,
            error=error,
            output=output,
            **kwargs,
        )

    @classmethod
    def success_result(
        cls,
        output: str,
        **kwargs: Any,
    ):
        return cls(
            success=True,
            output=output,
            error=None,
            **kwargs,
        )

    def to_model_output(self) -> str:
        if self.success:
            return self.output

        return f"ERROR: {self.error}\n\nOUTPUT:\n{self.output}"


def truncate_tool_result(
    result: "ToolResult",
    tool: "Tool",
    *,
    call_id: str | None = None,
) -> "ToolResult":
    """Cap a ToolResult's output at the tool's `max_result_size_chars`.

    Two paths:
      • call_id provided → persist overflow to disk via result_storage,
        replace `output` with a reference (the orchestration path).
      • call_id None → fall back to plain in-place truncation with a
        suffix (the registry path).

    Returns the result unchanged if it's already within the cap.
    """
    if len(result.output) <= tool.max_result_size_chars:
        return result

    if call_id is not None:
        from services.tools.result_storage import maybe_persist_large_result
        new_output = maybe_persist_large_result(
            output=result.output,
            tool_name=tool.name,
            tool_use_id=call_id,
            max_chars=tool.max_result_size_chars,
        )
    else:
        new_output = result.output[:tool.max_result_size_chars] + "\n... [output truncated]"

    return ToolResult(
        success=result.success,
        output=new_output,
        error=result.error,
        truncated=True,
        metadata=result.metadata,
        exit_code=result.exit_code,
    )


class Tool(abc.ABC):
    name: str = "base_tool"
    # Short tool summary — what shows up in the tool list. One sentence.
    description: str = "Base Tool"
    # Long model-facing behavioral guidance. Concatenated onto `description`
    # in the JSON schema sent to the model. Holds the verbatim Claude Code
    # per-tool prompt content. None = no extended guidance.
    prompt: str | None = None
    # default for the tool call, like if the grep tool return the 500k char that can eat up all the context window
    max_result_size_chars = 100_000
    kind: ToolKind = ToolKind.READ

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        super().__init__()

    @property
    def schema(self) -> dict[str, Any] | type[BaseModel]:
        raise NotImplementedError(
            "Tool must define schema property and other attributes"
        )

    @abc.abstractmethod
    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        raise NotImplementedError("Tool subclasses must implement execute()")

    def validate_param(self, params: dict[str, Any]) -> list[str]:
        schema = self.schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            # here isinstance check the schema is class not an object
            try:
                schema(**params)
            except ValidationError as e:
                errors = []
                for error in e.errors():
                    field = ".".join(str(x) for x in error.get("loc", ()))
                    message = error.get("msg")
                    errors.append(f"Parameter {field} : {message}")

                return errors

            except Exception as e:
                return [str(e)]

        return []

    def is_mutating(self, params: dict[str, Any]) -> bool:
        return self.kind in {
            ToolKind.WRITE,
            ToolKind.SHELL,
            ToolKind.NETWORK,
            ToolKind.MEMORY,
        }

    async def get_confirmations(
        self, invocation: ToolInvocation
    ) -> ToolConfirmation | None:
        if not self.is_mutating(invocation.params):
            return None

        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"Execute: {self.name}",
        )

    # there can be scenarios, where we can execute tool concurrently
    def is_concurrency_safe(self, params: dict) -> bool:
        return False

    # if read only we can skip permissions
    def is_read_only(self, params: dict) -> bool:
        return False

    # get extra warnings, if that's little distructive
    def is_destructive(self, params: dict) -> bool:
        return False

    # Value matched against rule sub-patterns like `Bash(npm install)` or
    # `Read(./src/*)`. The string here is what a rule's `ruleContent` is
    # matched against (Bash → command, file tools → file_path, search tools
    # → pattern). Returning None means this tool only supports tool-name
    # rules with no sub-pattern.
    # Claude Code parity: preparePermissionMatcher() in each tool returns a
    # `(pattern) => bool` closure; we expose the matched value instead and
    # let the rule matcher build the pattern check.
    def get_permission_value(self, params: dict) -> str | None:
        return None

    def to_openai_schema(self) -> dict[str, Any]:
        # Claude Code concatenates the per-tool prompt onto the description
        # so the model gets full behavioral guidance in the tools block.
        full_description = self.description
        if self.prompt:
            full_description = f"{self.description}\n\n{self.prompt}"

        schema = self.schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            json_schema = model_json_schema(schema, mode="serialization")

            return {
                "name": self.name,
                "description": full_description,
                "parameters": {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", []),
                },
            }

        if isinstance(schema, dict):
            result = {
                "name": self.name,
                "description": full_description,
            }

            if "parameters" in schema:
                result["parameters"] = schema["parameters"]
            else:
                result["parameters"] = schema

            return result

        raise ValueError(f"Invalid schema for the openai {self.name}: {type(schema)}")
