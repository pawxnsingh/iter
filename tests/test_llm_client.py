"""
LLMClient provider/deployment resolution tests.

Guards the Azure deployment-name routing fix: Azure OpenAI routes by deployment
name (decoupled from the model id), so the request `model` field must be the
deployment, not the config model name — otherwise Azure 404s as
DeploymentNotFound. OpenAI routes by model id and keeps the config model name.
"""

import pytest

from client.llm_client import LLMClient
from config.config import Config


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(var, raising=False)


def test_azure_request_model_uses_deployment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.2")

    config = Config()
    config.model_name = "gpt-5.2-chat"
    client = LLMClient(config)

    assert client._provider == "azure"
    assert client._request_model() == "gpt-5.2"


def test_azure_falls_back_to_model_name_when_no_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")

    config = Config()
    config.model_name = "gpt-5.2-chat"
    client = LLMClient(config)

    assert client._provider == "azure"
    assert client._request_model() == "gpt-5.2-chat"


def test_openai_ignores_deployment_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.2")

    config = Config()
    config.model_name = "gpt-4o"
    client = LLMClient(config)

    assert client._provider == "openai"
    assert client._request_model() == "gpt-4o"
