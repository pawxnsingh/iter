"""
The prompt is generated dynamically from a SUPPORTED_SETTINGS registry +
model options. We render a static equivalent matching the canonical shape.
"""

DESCRIPTION = "Get or set configuration settings."

CONFIG_TOOL_PROMPT = """View or change configuration settings. Use when the user requests configuration changes, asks about current settings, or when adjusting a setting would benefit them.

## Usage
- **Get current value:** Omit the "value" parameter
- **Set new value:** Include the "value" parameter

## Examples
- Get theme: { "setting": "theme" }
- Set dark theme: { "setting": "theme", "value": "dark" }
- Enable verbose: { "setting": "verbose", "value": true }
- Change model: { "setting": "model", "value": "opus" }
- Change permission mode: { "setting": "permissions.defaultMode", "value": "plan" }
"""
