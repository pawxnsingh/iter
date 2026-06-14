"""
Round-6 MCP parity regressions — the subsystem whose "result mapping verified"
mark was revoked. Pins tool naming, content mapping, and schema passthrough.
"""
from types import SimpleNamespace

from tools.mcp.client import _map_mcp_content, MCPToolInfo
from tools.mcp.mcp_manager import _build_mcp_tool_name, _normalize_name_for_mcp
from tools.mcp.mcp_tool import MCPTool


def test_build_mcp_tool_name():
    assert _build_mcp_tool_name("filesystem", "read_file") == "mcp__filesystem__read_file"


def test_normalize_name_for_mcp():
    assert _normalize_name_for_mcp("my server.v2") == "my_server_v2"
    assert _build_mcp_tool_name("ns.x", "a b") == "mcp__ns_x__a_b"


def test_map_text_content():
    items = [SimpleNamespace(type="text", text="hello")]
    text, images = _map_mcp_content(items)
    assert text == "hello" and images == []


def test_map_image_content_becomes_attachment():
    items = [SimpleNamespace(type="image", data="QUJD", mimeType="image/png")]
    text, images = _map_mcp_content(items, "srv")
    assert images == [{"media_type": "image/png", "data": "QUJD"}]
    assert text == ""
    assert "QUJD" not in text


def test_map_resource_text():
    res = SimpleNamespace(uri="file:///f.txt", text="filebody", blob=None, mimeType="text/plain")
    items = [SimpleNamespace(type="resource", resource=res)]
    text, images = _map_mcp_content(items, "srv")
    assert text == "[Resource from srv at file:///f.txt] filebody" and images == []


def test_map_resource_link():
    items = [SimpleNamespace(type="resource_link", uri="file:///x")]
    text, _ = _map_mcp_content(items)
    assert "[Resource link: file:///x]" in text


class _Cfg:
    pass


def _tool(info):
    return MCPTool(config=SimpleNamespace(), client=None, tool_info=info, name="mcp__s__t")


def test_schema_passthrough_keeps_defs():
    info = MCPToolInfo(name="t", description="d", input_schema={
        "type": "object",
        "properties": {"x": {"$ref": "#/$defs/X"}},
        "$defs": {"X": {"type": "string"}},
        "required": ["x"],
    })
    schema = _tool(info).schema
    assert "$defs" in schema
    assert schema["properties"]["x"]["$ref"] == "#/$defs/X"


def test_annotations_drive_concurrency_not_permission():
    ro = _tool(MCPToolInfo(name="t", description="d", read_only=True))
    assert ro.is_read_only({}) is False
    assert ro.is_concurrency_safe({}) is True
    assert ro.is_mutating({}) is False
    rw = _tool(MCPToolInfo(name="t", description="d", read_only=False))
    assert rw.is_mutating({}) is True
    assert rw.is_concurrency_safe({}) is False


def test_readonly_mcp_tool_asks_in_default_mode():
    import asyncio
    from config.config import Config
    from core_types.permissions import ToolPermissionContext, PermissionMode, PermissionBehavior
    from permissions.checker import PermissionChecker
    info = MCPToolInfo(name="read_file", description="d", read_only=True)
    t = MCPTool(config=Config(), client=None, tool_info=info, name="mcp__fs__read_file")
    ctx = ToolPermissionContext(mode=PermissionMode.DEFAULT, working_directories=("/tmp",))
    b = asyncio.get_event_loop().run_until_complete(PermissionChecker(ctx).check(t, {})).behavior
    assert b == PermissionBehavior.ASK


def test_mcp_server_level_and_wildcard_rules_match():
    from permissions.rules import match_rules_for_tool
    tool = "mcp__github__create_issue"
    assert match_rules_for_tool(tool, {"mcp__github": [""]})
    assert match_rules_for_tool(tool, {"mcp__github__*": [""]})
    assert match_rules_for_tool(tool, {"mcp__github__create_issue": [""]})
    assert not match_rules_for_tool(tool, {"mcp__gitlab": [""]})
