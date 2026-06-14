"""
Brutal tests for tools/base.py — ToolResult factories, FileDiff.to_diff,
Tool validation, ToolKind, schema conversion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_tool_result_success_factory() -> None:
    from tools.base import ToolResult

    r = ToolResult.success_result("ok", metadata={"a": 1})
    assert r.success is True
    assert r.output == "ok"
    assert r.error is None
    assert r.metadata == {"a": 1}
    assert r.truncated is False


def test_tool_result_error_factory_default_output() -> None:
    from tools.base import ToolResult

    r = ToolResult.error_result("boom")
    assert r.success is False
    assert r.error == "boom"
    assert r.output == ""


def test_tool_result_error_factory_with_output() -> None:
    from tools.base import ToolResult

    r = ToolResult.error_result("boom", output="stderr text", exit_code=1)
    assert r.success is False
    assert r.error == "boom"
    assert r.output == "stderr text"
    assert r.exit_code == 1


def test_tool_result_to_model_output_success() -> None:
    from tools.base import ToolResult

    r = ToolResult.success_result("done")
    assert r.to_model_output() == "done"


def test_tool_result_to_model_output_error_format() -> None:
    from tools.base import ToolResult

    r = ToolResult.error_result("bad", output="stderr")
    out = r.to_model_output()
    assert "ERROR: bad" in out
    assert "OUTPUT:" in out
    assert "stderr" in out


def test_filediff_normal_changes() -> None:
    from tools.base import FileDiff

    d = FileDiff(
        path=Path("/tmp/x.py"),
        old_content="a\nb\nc\n",
        new_content="a\nBETA\nc\n",
    )
    out = d.to_diff()
    assert "---" in out and "+++" in out
    assert "-b" in out
    assert "+BETA" in out


def test_filediff_new_file_uses_dev_null() -> None:
    from tools.base import FileDiff

    d = FileDiff(
        path=Path("/tmp/new.py"),
        old_content="",
        new_content="hello\n",
        is_new_file=True,
    )
    out = d.to_diff()
    assert "/dev/null" in out
    assert "+hello" in out


def test_filediff_deletion_uses_dev_null() -> None:
    from tools.base import FileDiff

    d = FileDiff(
        path=Path("/tmp/gone.py"),
        old_content="bye\n",
        new_content="",
        is_deletion=True,
    )
    out = d.to_diff()
    assert "/dev/null" in out
    assert "-bye" in out


def test_filediff_no_trailing_newline_handled() -> None:
    from tools.base import FileDiff

    d = FileDiff(
        path=Path("/tmp/x"),
        old_content="no newline",
        new_content="still no newline",
    )
    out = d.to_diff()
    assert "-no newline" in out
    assert "+still no newline" in out


def test_filediff_identical_produces_empty() -> None:
    from tools.base import FileDiff

    d = FileDiff(
        path=Path("/tmp/x"),
        old_content="same\n",
        new_content="same\n",
    )
    out = d.to_diff()
    assert "+" not in out.replace("+++", "")
    assert "-" not in out.replace("---", "")


class _DummySchema(BaseModel):
    path: str = Field(...)
    count: int = Field(default=1, ge=1)


def _make_config(tmp_path: Path):
    from config.config import Config

    return Config(cwd=tmp_path)


def _make_tool(schema_obj, tmp_path: Path):
    from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

    class _T(Tool):
        name = "dummy"
        description = "dummy tool"
        kind = ToolKind.READ

        @property
        def schema(self):
            return schema_obj

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("ok")

    return _T(config=_make_config(tmp_path))


def test_tool_validate_pydantic_accepts_good_params(tmp_path: Path) -> None:
    tool = _make_tool(_DummySchema, tmp_path)
    errors = tool.validate_param({"path": "/x", "count": 2})
    assert errors == []


def test_tool_validate_pydantic_rejects_missing_required(tmp_path: Path) -> None:
    tool = _make_tool(_DummySchema, tmp_path)
    errors = tool.validate_param({})
    assert errors
    assert any("path" in e for e in errors)


def test_tool_validate_pydantic_rejects_bad_constraint(tmp_path: Path) -> None:
    tool = _make_tool(_DummySchema, tmp_path)
    errors = tool.validate_param({"path": "/x", "count": 0})
    assert errors
    assert any("count" in e for e in errors)


def test_tool_validate_dict_schema_is_noop(tmp_path: Path) -> None:
    tool = _make_tool({"type": "object", "properties": {}}, tmp_path)
    assert tool.validate_param({"anything": 123}) == []


def test_tool_to_openai_schema_from_pydantic(tmp_path: Path) -> None:
    tool = _make_tool(_DummySchema, tmp_path)
    out = tool.to_openai_schema()
    assert out["name"] == "dummy"
    assert out["description"] == "dummy tool"
    assert out["parameters"]["type"] == "object"
    assert "path" in out["parameters"]["properties"]


def test_tool_to_openai_schema_from_dict(tmp_path: Path) -> None:
    tool = _make_tool({"type": "object", "properties": {"q": {"type": "string"}}}, tmp_path)
    out = tool.to_openai_schema()
    assert out["name"] == "dummy"
    assert out["parameters"] == {"type": "object", "properties": {"q": {"type": "string"}}}


def test_tool_to_openai_schema_invalid_raises(tmp_path: Path) -> None:
    tool = _make_tool(object(), tmp_path)
    with pytest.raises(ValueError):
        tool.to_openai_schema()


def test_tool_is_mutating_by_kind(tmp_path: Path) -> None:
    from tools.base import ToolKind
    from tools.base import Tool, ToolInvocation, ToolResult

    class _T(Tool):
        name = "x"

        @property
        def schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("")

    cfg = _make_config(tmp_path)

    for kind, mutating in [
        (ToolKind.READ, False),
        (ToolKind.WRITE, True),
        (ToolKind.SHELL, True),
        (ToolKind.NETWORK, True),
        (ToolKind.MEMORY, True),
        (ToolKind.MCP, False),
    ]:
        _T.kind = kind
        assert _T(cfg).is_mutating({}) is mutating, f"{kind} should be mutating={mutating}"


@pytest.mark.asyncio
async def test_tool_get_confirmations_read_only_returns_none(tmp_path: Path) -> None:
    from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

    class _T(Tool):
        name = "r"
        kind = ToolKind.READ

        @property
        def schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("")

    t = _T(_make_config(tmp_path))
    inv = ToolInvocation(params={}, cwd=tmp_path)
    assert await t.get_confirmations(inv) is None


@pytest.mark.asyncio
async def test_tool_get_confirmations_write_returns_confirmation(tmp_path: Path) -> None:
    from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

    class _T(Tool):
        name = "w"
        kind = ToolKind.WRITE

        @property
        def schema(self):
            return {"type": "object", "properties": {}}

        async def execute(self, invocation: ToolInvocation) -> ToolResult:
            return ToolResult.success_result("")

    t = _T(_make_config(tmp_path))
    inv = ToolInvocation(params={"path": "/x"}, cwd=tmp_path)
    conf = await t.get_confirmations(inv)
    assert conf is not None
    assert conf.tool_name == "w"
