"""The LLM layer: one small interface, several providers.

Every provider has the same method:

    reply = provider.complete(messages)      # messages = [{"role": ..., "content": ...}]

so the rest of the agent never knows (or cares) which LLM is behind it.
"""

from .base import DailyLimitReached, LLMError, LLMProvider, LLMReply, RequestTooLarge, estimate_tokens


def make_provider(cfg) -> LLMProvider:
    if cfg.provider == "scripted":
        from .scripted import ScriptedProvider
        return ScriptedProvider(cfg.scripted_dir)
    from .openai_compat import OpenAICompatibleProvider
    return OpenAICompatibleProvider(cfg)


__all__ = ["make_provider", "LLMProvider", "LLMReply", "LLMError", "DailyLimitReached",
           "RequestTooLarge", "estimate_tokens"]
