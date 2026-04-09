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


class Tool(abc.ABC):
    name: str = "base_tool"
    description: str = "Base Tool"
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
        pass

    def validate_param(self, params: dict[str, Any]) -> list[str]:
        schema = self.schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            # here isinstance check the schema is class not an object
            try:
                schema(**params)
            except ValidationError as e:
                errors = []
                for error in e.errors():
                    print(error)
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

    def to_openai_schema(self) -> dict[str, Any]:
        schema = self.schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            json_schema = model_json_schema(schema, mode="serialization")

            return {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", []),
                },
            }

        if isinstance(schema, dict):
            result = {
                "name": self.name,
                "description": self.description,
            }

            if "parameters" in schema:
                result["parameters"] = schema["parameters"]
            else:
                result["parameters"] = schema

            return result

        raise ValueError(f"Invalid schema for the openai {self.name}: {type(schema)}")
