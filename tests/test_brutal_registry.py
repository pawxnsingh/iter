"""
Brutal tests for tools/registry.py — registration, lookup, invocation,
allowed_tools filtering, error paths, truncation enforcement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Test fixtures ─────────────────────────────────────────────────────────


def _mk_config(tmp_path: Path, allowed: list[str] | None = None):
    from config.config import Config

    return Config(cwd=tmp_path, allowed_tools=allowed)


def _mk_echo_tool(config):
    from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

    class EchoTool(Tool):
        name = "Echo"
        description = "Echoes its input"
        kind = ToolKind.READ
        max_result_size_chars = 50  # small, to exercise truncation

        @property
        def schema(self):
            return {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result(invocation.params.get("text", ""))

    return EchoTool(config)


def _mk_bigoutput_tool(config):
    from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

    class BigTool(Tool):
        name = "Big"
        description = "Returns huge output"
        kind = ToolKind.READ
        max_result_size_chars = 20  # cap at 20 chars

        @property
        def schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("x" * 1000)

    return BigTool(config)


def _mk_crash_tool(config):
    from tools.base import Tool, ToolInvocation, ToolResult

    class CrashTool(Tool):
        name = "Crash"
        description = "Raises"

        @property
        def schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            raise RuntimeError("intentional")

    return CrashTool(config)


# ─── Registration ──────────────────────────────────────────────────────────


def test_registry_empty_by_default(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    r = ToolRegistry(_mk_config(tmp_path))
    assert r.get_tools() == []
    assert r.get("anything") is None


def test_registry_register_and_get(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    tool = _mk_echo_tool(cfg)
    r.register(tool)
    assert r.get("Echo") is tool
    assert tool in r.get_tools()


def test_registry_replace_on_reregister(tmp_path: Path, caplog) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    t1 = _mk_echo_tool(cfg)
    t2 = _mk_echo_tool(cfg)
    r.register(t1)
    r.register(t2)  # second call should replace
    assert r.get("Echo") is t2


def test_registry_unregister(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_mk_echo_tool(cfg))
    assert r.unregister("Echo") is True
    assert r.unregister("Echo") is False
    assert r.get("Echo") is None


def test_registry_allowed_tools_filters_listing(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path, allowed=["Big"])
    r = ToolRegistry(cfg)
    r.register(_mk_echo_tool(cfg))
    r.register(_mk_bigoutput_tool(cfg))
    names = [t.name for t in r.get_tools()]
    assert names == ["Big"]


def test_registry_allowed_tools_none_lists_all(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path, allowed=None)
    r = ToolRegistry(cfg)
    r.register(_mk_echo_tool(cfg))
    r.register(_mk_bigoutput_tool(cfg))
    assert len(r.get_tools()) == 2


def test_registry_get_schemas(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_mk_echo_tool(cfg))
    schemas = r.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "Echo"
    assert schemas[0]["parameters"]["type"] == "object"


# ─── Invocation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_invoke_unknown_tool_returns_error(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    r = ToolRegistry(_mk_config(tmp_path))
    result = await r.invoke("Nope", {}, cwd=tmp_path)
    assert result.success is False
    assert result.error and "Unknown" in result.error
    assert result.metadata["tool_name"] == "Nope"


@pytest.mark.asyncio
async def test_registry_invoke_validation_error(tmp_path: Path) -> None:
    from pydantic import BaseModel, Field
    from tools.base import Tool, ToolInvocation, ToolResult
    from tools.registry import ToolRegistry

    class Params(BaseModel):
        text: str = Field(...)

    class _T(Tool):
        name = "Strict"
        description = ""

        @property
        def schema(self):
            return Params

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("unreachable")

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_T(cfg))
    result = await r.invoke("Strict", {}, cwd=tmp_path)
    assert result.success is False
    assert "Invalidation" in (result.error or "")
    assert "validation_error" in result.metadata


@pytest.mark.asyncio
async def test_registry_invoke_happy_path(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_mk_echo_tool(cfg))
    result = await r.invoke("Echo", {"text": "ping"}, cwd=tmp_path)
    assert result.success is True
    assert result.output == "ping"


@pytest.mark.asyncio
async def test_registry_invoke_catches_tool_exceptions(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_mk_crash_tool(cfg))
    result = await r.invoke("Crash", {}, cwd=tmp_path)
    assert result.success is False
    assert result.error and "intentional" in result.error


@pytest.mark.asyncio
async def test_registry_enforces_max_result_size(tmp_path: Path) -> None:
    from tools.registry import ToolRegistry

    cfg = _mk_config(tmp_path)
    r = ToolRegistry(cfg)
    r.register(_mk_bigoutput_tool(cfg))
    result = await r.invoke("Big", {}, cwd=tmp_path)
    assert result.success is True
    assert result.truncated is True
    # max_result_size_chars=20, plus the truncation suffix
    assert len(result.output) <= 20 + len("\n... [output truncated]") + 5


# ─── Default registry wiring ──────────────────────────────────────────────


def test_default_registry_has_builtin_tools(tmp_path: Path) -> None:
    from tools.registry import create_default_registry

    r = create_default_registry(_mk_config(tmp_path))
    names = [t.name for t in r.get_tools()]
    assert len(names) > 0
    # Should have the core builtin tools (subset check)
    for expected in ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]:
        assert expected in names, f"missing core tool: {expected}"
