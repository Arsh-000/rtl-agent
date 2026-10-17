"""Shared pieces of the LLM layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMReply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    cut_off: bool = False        # the reply stopped at the output-token limit (incomplete)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(Exception):
    """The LLM call failed (network, bad key, server error, ...)."""


class DailyLimitReached(LLMError):
    """The daily token/request budget is used up. The run is saved and can be resumed."""


class RequestTooLarge(LLMError):
    """One request would not fit in the per-minute token limit or the model's context."""


class LLMProvider:
    name = "base"
    tokens_used = 0

    def complete(self, messages: list[dict], max_tokens: int | None = None,
                 overrides: dict | None = None) -> LLMReply:
        """overrides: extra request parameters for this one call, e.g. {"reasoning_effort": "low"}."""
        raise NotImplementedError


def estimate_tokens(text: str) -> int:
    """Rough token count: about 4 characters per token for English and code.

    Good enough to stay under rate limits; the API's own count (returned
    with every reply) is what we record.
    """
    return len(text) // 4 + 1
