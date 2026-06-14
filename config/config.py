from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core_types.permissions import PermissionMode


class ModelConfig(BaseModel):
    name: str = Field(default="gpt-5.2", alias="model")
    temperature: float = Field(
        default=1,
        ge=0.0,
        le=2.0,
    )
    context_window: int = 400_000


class ShellEnvironmentPolicy(BaseModel):
    ignore_default_excludes: bool = False
    exclude_patterns: list = Field(
        default_factory=lambda: ["*KEY*", "*SECRET*", "*TOKEN*"]
    )
    set_vars: dict[str, Any] = Field(default_factory=dict)


class MCPServerConfig(BaseModel):
    enabled: bool = True
    startup_timeout_sec: float = 10

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: Path | None = None

    url: str | None = None
    type: str | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> MCPServerConfig:
        has_command = self.command is not None
        has_url = self.url is not None

        if not has_command and not has_url:
            raise ValueError(
                "MCP Server must have either 'command' (stdio) or 'url' (http/sse)"
            )

        if has_command and has_url:
            raise ValueError(
                "MCP Server cannot have both 'command' (stdio) and 'url' (http/sse)"
            )

        return self


class Config(BaseModel):
    model: ModelConfig = Field(
        default_factory=ModelConfig,
    )
    cwd: Path = Field(default_factory=Path.cwd)
    shell_environment: ShellEnvironmentPolicy = Field(
        default_factory=ShellEnvironmentPolicy
    )
    max_turns: int = Field(default=100)
    task_budget_tokens: int | None = Field(default=None)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    allowed_tools: list[str] | None = Field(
        None,
        description="If set, only these tools will be available to the agent",
    )

    allow_rules: dict[str, list[str]] = Field(default_factory=dict)
    deny_rules: dict[str, list[str]] = Field(default_factory=dict)
    ask_rules: dict[str, list[str]] = Field(default_factory=dict)

    developer_instructions: str | None = None
    user_instructions: str | None = None

    permission_mode: PermissionMode = Field(default=PermissionMode.DEFAULT)

    debug: bool = Field(default=False)

    model_config = ConfigDict(validate_assignment=True, use_enum_values=False)

    @property
    def api_key(self) -> str:
        return os.environ.get("AZURE_OPENAI_API_KEY")

    @property
    def base_url(self) -> str:
        return os.environ.get("AZURE_OPENAI_ENDPOINT")

    @property
    def api_version(self) -> str:
        return os.environ.get("AZURE_OPENAI_API_VERSION")

    @property
    def fallback_model(self) -> str | None:
        """Optional fallback model/deployment used when the primary is
        repeatedly overloaded. None disables it."""
        return os.environ.get("LLM_FALLBACK_MODEL") or None

    @property
    def azure_deployment(self) -> str | None:
        """Azure deployment name for the request `model` field.

        Azure OpenAI routes by deployment name, which is decoupled from the
        underlying model id (a deployment can be named anything). When set, it
        MUST be used as the request `model`; the config model name is not a
        valid Azure deployment and yields a 404 DeploymentNotFound.
        """
        return os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")

    @property
    def model_name(self) -> str:
        return self.model.name

    @model_name.setter
    def model_name(self, value: str) -> None:
        self.model.name = value

    @property
    def temperature(self) -> float:
        return self.model.temperature

    @temperature.setter
    def temperature(self, value: float) -> None:
        self.model.temperature = value

    def validate(self) -> list[str]:
        """Validate config. Auto-detects provider from env vars."""
        errors: list[str] = []

        has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        has_azure = bool(self.api_key and self.base_url)

        if not has_anthropic and not has_openai and not has_azure:
            errors.append(
                "No LLM provider configured. Set one of:\n"
                "  - ANTHROPIC_API_KEY (for Anthropic)\n"
                "  - OPENAI_API_KEY (for OpenAI)\n"
                "  - AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT (for Azure)"
            )

        if not self.cwd.exists():
            errors.append(f"Working directory does not exist: {self.cwd}")

        return errors

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
