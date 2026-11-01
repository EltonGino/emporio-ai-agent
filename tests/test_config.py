from datetime import datetime, time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from emporio_agent.config import (
    CLIENT_CONFIG_PATH,
    PROJECT_ROOT,
    ClientConfig,
    Settings,
    load_client_config,
)

ENV_VARS = [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "AGENT_NOW",
    "DEMO_MODE",
    "DATABASE_URL",
    "CHECKPOINT_DB",
    "HISTORY_MAX_MESSAGES",
    "LLM_FALLBACK_MODELS",
    "LLM_REASONING_EFFORT",
    "MAX_MESSAGES_PER_SESSION",
    "DAILY_MESSAGE_CAP",
]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_defaults(clean_env):
    settings = Settings()
    assert settings.llm_provider == "groq"
    assert settings.agent_now is None
    assert settings.demo_mode is False
    assert settings.history_max_messages == 20
    assert settings.llm_model == "openai/gpt-oss-120b"
    assert settings.llm_fallback_models == ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    assert settings.llm_reasoning_effort == "low"
    assert settings.max_messages_per_session == 15


def test_fallback_models_are_comma_separated(clean_env):
    clean_env.setenv("LLM_FALLBACK_MODELS", "qwen/qwen3.8-27b, openai/gpt-oss-20b ,")
    assert Settings().llm_fallback_models == ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]


def test_invalid_reasoning_effort_is_rejected(clean_env):
    clean_env.setenv("LLM_REASONING_EFFORT", "max")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_from_env(clean_env):
    clean_env.setenv("AGENT_NOW", "2026-03-25T10:30")
    clean_env.setenv("DEMO_MODE", "true")
    clean_env.setenv("HISTORY_MAX_MESSAGES", "10")
    settings = Settings()
    assert settings.agent_now == datetime(2026, 3, 25, 10, 30)
    assert settings.demo_mode is True
    assert settings.history_max_messages == 10


def test_empty_env_values_mean_default(clean_env):
    clean_env.setenv("AGENT_NOW", "")
    clean_env.setenv("LLM_MODEL", "")
    settings = Settings()
    assert settings.agent_now is None
    assert settings.llm_model == "openai/gpt-oss-120b"


def test_relative_paths_resolve_from_project_root(clean_env):
    settings = Settings()
    assert settings.database_url == f"sqlite:///{PROJECT_ROOT}/data/runtime/store.db"
    assert settings.checkpoint_db == PROJECT_ROOT / "data/runtime/checkpoints.db"


def test_non_sqlite_database_url_is_untouched(clean_env):
    url = "postgresql+psycopg://user:pw@db:5432/store"
    clean_env.setenv("DATABASE_URL", url)
    assert Settings().database_url == url


def test_client_config_loads():
    client = load_client_config()
    assert client.store.name == "Empório da Música"
    assert client.store.tz.key == "America/Campo_Grande"
    assert client.store.hours["mon"] == (time(9), time(18))
    assert client.store.hours["sat"] == (time(9), time(13))
    assert client.store.hours["sun"] is None
    assert client.assistant.name == "Tom"
    assert client.assistant.persona_prompt.is_absolute()
    assert client.payments.pix_discount_percent == 5
    assert [b.max_installments for b in client.payments.installment_bands] == [3, 6, 12]
    assert client.catalog.partner_stores == []
    assert len(client.returns.ineligible_conditions) == 3
    assert client.retrieval.documents == [PROJECT_ROOT / "data/raw/politicas_da_loja.pdf"]


@pytest.mark.parametrize(
    ("line", "section_id"),
    [
        ("4.2 Trocas por Defeito", "4.2"),
        ("3. Formas de Pagamento", "3"),
        ("7.1 Diretrizes de Atendimento", "7.1"),
    ],
)
def test_heading_pattern_matches_manual_headings(line, section_id):
    match = load_client_config().retrieval.heading_pattern.match(line)
    assert match and match.group(1) == section_id


def test_heading_pattern_ignores_body_text():
    pattern = load_client_config().retrieval.heading_pattern
    assert not pattern.match("• Frete grátis para pedidos acima de R$ 500,00.")


def _raw_client_config() -> dict:
    return yaml.safe_load(Path(CLIENT_CONFIG_PATH).read_text(encoding="utf-8"))


def test_installment_bands_must_be_contiguous():
    raw = _raw_client_config()
    raw["payments"]["installment_bands"][1]["from"] = 5
    with pytest.raises(ValidationError, match="contiguous"):
        ClientConfig.model_validate(raw)


def test_unknown_timezone_is_rejected():
    raw = _raw_client_config()
    raw["store"]["timezone"] = "America/Nowhere"
    with pytest.raises(ValidationError, match="unknown timezone"):
        ClientConfig.model_validate(raw)


def test_every_weekday_needs_hours():
    raw = _raw_client_config()
    del raw["store"]["hours"]["sun"]
    with pytest.raises(ValidationError, match="hours missing"):
        ClientConfig.model_validate(raw)
