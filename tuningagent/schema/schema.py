from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class LLMProvider(str, Enum):
    """LLM provider types."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class FunctionCall(BaseModel):
    """Function call details."""

    name: str
    arguments: dict[str, Any]  # Function arguments as dict


class ToolCall(BaseModel):
    """Tool call structure."""

    id: str
    type: str  # "function"
    function: FunctionCall


class Message(BaseModel):
    """Chat message."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | list[dict[str, Any]]  # Can be string or list of content blocks
    thinking: str | None = None  # Extended thinking content for assistant messages
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # For tool role


class TokenUsage(BaseModel):
    """Token usage statistics from LLM API response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """LLM response."""

    content: str
    thinking: str | None = None  # Extended thinking blocks
    tool_calls: list[ToolCall] | None = None
    finish_reason: str
    usage: TokenUsage | None = None  # Token usage from API response


class ModelStats(BaseModel):
    """Per-model execution statistics."""

    model_alias: str
    model_name: str
    provider: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    call_count: int = 0
    error_count: int = 0
    total_latency_s: float = 0.0

    @property
    def avg_latency_s(self) -> float:
        return self.total_latency_s / self.call_count if self.call_count else 0.0

    def record_call(self, usage: TokenUsage | None, latency_s: float, error: bool = False):
        self.call_count += 1
        self.total_latency_s += latency_s
        if error:
            self.error_count += 1
        if usage:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.total_tokens += usage.total_tokens


class HealthCheckResult(BaseModel):
    """Result of a single model health check."""

    alias: str
    model_name: str
    provider: str
    available: bool
    latency_ms: float = 0.0
    error: str | None = None
