from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1)
    system_prompt: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed or len(trimmed) > 128:
            raise ValueError("session_id must be between 1 and 128 characters")
        return trimmed

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("message cannot be empty or only whitespace")
        if len(trimmed) > 4000:
            raise ValueError("message cannot exceed 4000 characters")
        return trimmed


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HealthResponse(BaseModel):
    status: str = "ok"
    keys_configured: int
    cache_enabled: bool


class SessionMessageMetadata(BaseModel):
    resolution_source: str
    input_tokens: int
    output_tokens: int


class SessionMessageItem(BaseModel):
    role: str
    content: str
    metadata: Optional[SessionMessageMetadata] = None

    def model_dump(self, *args, **kwargs):
        # Omit metadata if None (as required for user messages)
        d = super().model_dump(*args, **kwargs)
        if d.get("metadata") is None:
            d.pop("metadata", None)
        return d


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: List[SessionMessageItem]
