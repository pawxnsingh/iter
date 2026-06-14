"""
Config tool — get/set configuration from within the agent.

Source: src/tools/ConfigTool/

Allows the model to read and modify settings at runtime.
Supports: model, temperature, permission mode, max turns, etc.
"""


from pydantic import BaseModel, Field

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.builtin.ConfigTool.prompt import CONFIG_TOOL_PROMPT, DESCRIPTION

CONFIGURABLE_SETTINGS = {
    "model": "The LLM model to use",
    "temperature": "Model temperature (0.0-2.0)",
    "permission_mode": "Permission mode (default/plan/bypass/accept_edits/dont_ask)",
    "max_turns": "Maximum agent turns per message",
    "debug": "Enable debug logging (true/false)",
}


class ConfigToolParams(BaseModel):
    setting: str = Field(
        ...,
        description=f"Setting name. Available: {', '.join(CONFIGURABLE_SETTINGS.keys())}",
    )
    value: str | None = Field(
        None,
        description="New value. Omit to get current value.",
    )


class ConfigTool(Tool):
    name = "Config"
    description = DESCRIPTION
    prompt = CONFIG_TOOL_PROMPT
    kind = ToolKind.MEMORY
    schema = ConfigToolParams

    def is_read_only(self, params: dict) -> bool:
        return params.get("value") is None

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ConfigToolParams(**invocation.params)

        if params.setting not in CONFIGURABLE_SETTINGS:
            return ToolResult.error_result(
                f"Unknown setting: {params.setting}\n"
                f"Available: {', '.join(CONFIGURABLE_SETTINGS.keys())}"
            )

        if params.value is None:
            value = self._get_setting(params.setting)
            return ToolResult.success_result(
                f"{params.setting} = {value}"
            )

        try:
            self._set_setting(params.setting, params.value)
            return ToolResult.success_result(
                f"{params.setting} updated to: {params.value}"
            )
        except ValueError as e:
            return ToolResult.error_result(str(e))

    def _get_setting(self, name: str) -> str:
        match name:
            case "model": return self.config.model_name
            case "temperature": return str(self.config.temperature)
            case "permission_mode": return self.config.permission_mode
            case "max_turns": return str(self.config.max_turns)
            case "debug": return str(self.config.debug)
            case _: return "unknown"

    def _set_setting(self, name: str, value: str) -> None:
        match name:
            case "model":
                self.config.model_name = value
            case "temperature":
                t = float(value)
                if not 0 <= t <= 2:
                    raise ValueError("Temperature must be 0.0-2.0")
                self.config.temperature = t
            case "permission_mode":
                valid = {"default", "plan", "bypass", "accept_edits", "dont_ask"}
                if value not in valid:
                    raise ValueError(f"Must be one of: {valid}")
                self.config.permission_mode = value
            case "max_turns":
                self.config.max_turns = int(value)
            case "debug":
                self.config.debug = value.lower() in ("true", "1", "yes")
