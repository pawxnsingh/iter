"""
Brutal tests for config/loader.py + config/config.py — merging, bad TOML,
missing provider keys, Config.validate().
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# Config.validate()
# ═══════════════════════════════════════════════════════════════════════════


def test_config_validate_requires_any_provider(tmp_path: Path, monkeypatch) -> None:
    from config.config import Config

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
                "AZURE_OPENAI_API_VERSION"):
        monkeypatch.delenv(key, raising=False)

    c = Config(cwd=tmp_path)
    errors = c.validate()
    assert errors
    assert any("provider" in e.lower() for e in errors)


def test_config_validate_anthropic_only(tmp_path: Path, monkeypatch) -> None:
    from config.config import Config

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    c = Config(cwd=tmp_path)
    assert c.validate() == []


def test_config_validate_bad_cwd(tmp_path: Path, monkeypatch) -> None:
    from config.config import Config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    missing = tmp_path / "never-existed"
    c = Config(cwd=missing)
    errors = c.validate()
    assert any("does not exist" in e for e in errors)


def test_config_model_name_property_roundtrip(tmp_path: Path) -> None:
    from config.config import Config

    c = Config(cwd=tmp_path)
    original = c.model_name
    c.model_name = "gpt-5-different"
    assert c.model_name == "gpt-5-different"
    c.model_name = original


def test_config_temperature_property_roundtrip(tmp_path: Path) -> None:
    from config.config import Config

    c = Config(cwd=tmp_path)
    c.temperature = 0.3
    assert c.temperature == 0.3


def test_config_to_dict_is_json_safe(tmp_path: Path) -> None:
    import json
    from config.config import Config

    c = Config(cwd=tmp_path)
    d = c.to_dict()
    # Should round-trip through JSON
    dumped = json.dumps(d)
    assert isinstance(dumped, str)


# ═══════════════════════════════════════════════════════════════════════════
# config/loader.py
# ═══════════════════════════════════════════════════════════════════════════


def test_load_config_no_files(tmp_path: Path, monkeypatch) -> None:
    """With no config files anywhere, load_config should return defaults."""
    from config import loader

    # Redirect system config to a nonexistent place
    monkeypatch.setattr(
        loader,
        "get_system_config_path",
        lambda: tmp_path / "no-such" / "config.toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    cfg = loader.load_config(cwd=tmp_path)
    assert cfg.cwd == tmp_path
    assert cfg.developer_instructions is None


def test_load_config_picks_up_project_agent_md(tmp_path: Path, monkeypatch) -> None:
    from config import loader

    monkeypatch.setattr(
        loader,
        "get_system_config_path",
        lambda: tmp_path / "no-such" / "config.toml",
    )
    (tmp_path / "AGENT.md").write_text("# project instructions", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    cfg = loader.load_config(cwd=tmp_path)
    assert cfg.developer_instructions is not None
    assert "project instructions" in cfg.developer_instructions


def test_load_config_project_toml_overrides_cwd(tmp_path: Path, monkeypatch) -> None:
    from config import loader

    project_dir = tmp_path / ".ai-agent"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text(
        'max_turns = 7\n[model]\nmodel = "custom-model"\ntemperature = 0.25\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        loader,
        "get_system_config_path",
        lambda: tmp_path / "no-such" / "config.toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    cfg = loader.load_config(cwd=tmp_path)
    assert cfg.max_turns == 7
    assert cfg.model_name == "custom-model"
    assert cfg.temperature == 0.25


def test_load_config_invalid_toml_is_skipped(tmp_path: Path, monkeypatch, caplog) -> None:
    """Malformed project TOML should log warning, not crash."""
    from config import loader

    project_dir = tmp_path / ".ai-agent"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text("this is not [[valid toml", encoding="utf-8")

    monkeypatch.setattr(
        loader,
        "get_system_config_path",
        lambda: tmp_path / "no-such" / "config.toml",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    cfg = loader.load_config(cwd=tmp_path)
    assert cfg.cwd == tmp_path  # still loaded defaults


def test_merge_dicts_nested() -> None:
    from config.loader import _merge_dicts

    a = {"x": 1, "nested": {"a": 1, "b": 2}}
    b = {"nested": {"b": 99, "c": 3}, "y": 4}
    out = _merge_dicts(a, b)
    assert out == {"x": 1, "y": 4, "nested": {"a": 1, "b": 99, "c": 3}}


def test_merge_dicts_override_type_mismatch() -> None:
    from config.loader import _merge_dicts

    # Override changes type from dict to scalar — scalar wins
    a = {"k": {"inner": 1}}
    b = {"k": "string"}
    assert _merge_dicts(a, b) == {"k": "string"}


def test_merge_dicts_base_unmutated() -> None:
    from config.loader import _merge_dicts

    base = {"a": {"x": 1}}
    override = {"a": {"y": 2}}
    _merge_dicts(base, override)
    assert base == {"a": {"x": 1}}  # base must not be mutated
