"""Scripted provider: replays hand-written responses from a folder.

THIS IS NOT AN LLM. It exists so the whole loop can be tested offline and
repeatably (no API key, no internet, same result every time). The files
001.md, 002.md, ... are returned in order, one per call. When they run
out, it answers "NO_MORE_IDEAS" and the controller stops.

Every report produced with this provider says so clearly.
"""

from __future__ import annotations

from pathlib import Path

from .base import LLMError, LLMProvider, LLMReply, estimate_tokens


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, folder: Path | None):
        if not folder or not Path(folder).is_dir():
            raise LLMError(f"scripted provider needs llm.scripted_dir pointing to a folder (got {folder})")
        self.files = sorted(Path(folder).glob("*.md"))
        self.index = 0
        self.tokens_used = 0

    def complete(self, messages: list[dict], max_tokens: int | None = None,
                 overrides: dict | None = None) -> LLMReply:
        prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
        if self.index >= len(self.files):
            text = "NO_MORE_IDEAS"
        else:
            text = self.files[self.index].read_text()
            self.index += 1
        reply = LLMReply(text=text, prompt_tokens=prompt_tokens,
                         completion_tokens=estimate_tokens(text), model="scripted (not an LLM)")
        self.tokens_used += reply.total_tokens
        return reply
