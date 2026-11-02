"""Request/response models of the HTTP API — the contract the frontend relies on
(frontend/src/api/types.ts). Chat responses are SSE streams of `core.events.AgentEvent`."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"  # same rule as the JSONL trace file names
MAX_MESSAGE_CHARS = 1000


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    message: str = Field(max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message is empty")
        return value


class ConfigResponse(BaseModel):
    assistant_name: str
    store_name: str
    slogan: str
    demo_mode: bool
    simulated_now: str | None  # "YYYY-MM-DDTHH:MM" in store time when the clock is frozen


class ErrorResponse(BaseModel):
    detail: str  # a friendly pt-BR message the UI shows as is
