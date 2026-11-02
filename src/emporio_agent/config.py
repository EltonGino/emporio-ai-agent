"""Runtime settings (environment / .env) and the client layer (config/client.yaml)."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENT_CONFIG_PATH = PROJECT_ROOT / "config" / "client.yaml"
INDEX_DIR = PROJECT_ROOT / "data" / "runtime" / "index"  # chunks + vectors, rebuilt by script
MODEL_CACHE_DIR = PROJECT_ROOT / "data" / "runtime" / "models"  # embedding model download

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ReturnReason = Literal["arrependimento", "defeito", "preferencia"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def resolve_path(path: Path) -> Path:
    """Relative paths are relative to the project root, not the current directory."""
    return path if path.is_absolute() else PROJECT_ROOT / path


# --- Runtime settings ---------------------------------------------------------------------


class Settings(BaseSettings):
    """Deployment settings. Read from the process environment (see `get_settings`)."""

    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    # Tried in order when the current model is rate limited (each has its own Groq quota).
    # Comma-separated in the environment.
    llm_fallback_models: Annotated[list[str], NoDecode] = [
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]
    llm_temperature: float = 0.2
    # Reasoning tokens count against the rate limits, so keep reasoning short.
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    database_url: str = "sqlite:///data/runtime/store.db"
    checkpoint_db: Path = Path("data/runtime/checkpoints.db")
    agent_now: datetime | None = None  # naive = store timezone
    demo_mode: bool = False
    max_messages_per_session: int = Field(default=15, ge=1)
    daily_message_cap: int = Field(default=60, ge=1)  # all sessions, per store-local day
    session_retention_days: int = Field(default=30, ge=1)
    history_max_messages: int = Field(default=20, ge=2)
    history_max_tokens: int = Field(default=2500, ge=200)
    # On HTTP 429, wait up to this long (provider's retry-after) and retry the same model once;
    # a longer wait falls back to the next model instead.
    rate_limit_max_wait_seconds: float = Field(default=15.0, ge=0)

    @field_validator("llm_fallback_models", mode="before")
    @classmethod
    def _split_models(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _absolute_sqlite_path(cls, url: str) -> str:
        prefix = "sqlite:///"
        if url.startswith(prefix) and url != prefix + ":memory:":
            return prefix + str(resolve_path(Path(url.removeprefix(prefix))))
        return url

    @field_validator("checkpoint_db")
    @classmethod
    def _absolute_checkpoint_path(cls, path: Path) -> Path:
        return resolve_path(path)


@lru_cache
def get_settings() -> Settings:
    """Load `.env` into the process environment, then build the settings.

    `.env` goes into `os.environ` (without overriding real env vars) so provider SDKs that
    read their own variables (GROQ_API_KEY, LANGSMITH_*) see them too.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return Settings()


# --- Client layer (config/client.yaml) ----------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HolidaysConfig(_Strict):
    country: str
    subdiv: str | None = None
    extra: list[date] = []  # municipal holidays (ISO dates in YAML)


class StoreConfig(_Strict):
    name: str
    slogan: str
    timezone: str
    address: str
    phone_whatsapp: str
    email: str
    holidays: HolidaysConfig
    hours: dict[Weekday, tuple[time, time] | None]

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:  # a KeyError, which pydantic would not wrap
            raise ValueError(f"unknown timezone {name!r}") from exc
        return name

    @field_validator("hours")
    @classmethod
    def _valid_hours(
        cls, hours: dict[Weekday, tuple[time, time] | None]
    ) -> dict[Weekday, tuple[time, time] | None]:
        missing = [day for day in WEEKDAYS if day not in hours]
        if missing:
            raise ValueError(f"hours missing for {missing} (use null for closed days)")
        for day, span in hours.items():
            if span is not None and span[0] >= span[1]:
                raise ValueError(f"hours for {day}: opening must be before closing")
        return hours

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class AssistantConfig(_Strict):
    name: str
    persona_prompt: Path

    @field_validator("persona_prompt")
    @classmethod
    def _absolute(cls, path: Path) -> Path:
        return resolve_path(path)


class CatalogConfig(_Strict):
    sells_only_instruments: bool
    non_sold_accessories: list[str]
    partner_stores: list[str] = []


class InstallmentBand(_Strict):
    model_config = ConfigDict(populate_by_name=True)

    min_installments: int = Field(alias="from", ge=1)
    max_installments: int = Field(alias="to", ge=1)
    min_installment: float = Field(gt=0)  # minimum value of each installment, BRL


class PaymentsConfig(_Strict):
    pix_discount_percent: float = Field(ge=0, lt=100)
    pix_applies_to_promotional: bool
    installment_bands: list[InstallmentBand]
    combined_payment_min_total: float
    boleto_clearing_business_days: int

    @model_validator(mode="after")
    def _contiguous_bands(self) -> PaymentsConfig:
        expected_start = 1
        for band in self.installment_bands:
            if band.min_installments != expected_start or band.max_installments < expected_start:
                raise ValueError("installment_bands must be ordered and contiguous from 1")
            expected_start = band.max_installments + 1
        return self


class ReturnsConfig(_Strict):
    regret_days_after_receipt: int
    preference_days_after_receipt: int
    defect_days_after_purchase: int
    legal_warranty_days_after_receipt: int
    ineligible_conditions: list[str]
    item_conditions: dict[ReturnReason, list[str]]


class SupportConfig(_Strict):
    complaint_return_business_hours: int


class RetrievalConfig(_Strict):
    documents: list[Path]
    heading_pattern: re.Pattern[str]
    top_k: int = Field(ge=1)

    @field_validator("documents")
    @classmethod
    def _absolute(cls, paths: list[Path]) -> list[Path]:
        return [resolve_path(p) for p in paths]


class ClientConfig(_Strict):
    store: StoreConfig
    assistant: AssistantConfig
    catalog: CatalogConfig
    payments: PaymentsConfig
    returns: ReturnsConfig
    support: SupportConfig
    retrieval: RetrievalConfig


def load_client_config(path: Path = CLIENT_CONFIG_PATH) -> ClientConfig:
    with path.open(encoding="utf-8") as f:
        return ClientConfig.model_validate(yaml.safe_load(f))


@lru_cache
def get_client_config() -> ClientConfig:
    return load_client_config()
