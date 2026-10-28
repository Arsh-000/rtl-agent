"""Talks to any OpenAI-compatible chat API: Groq, OpenAI, OpenRouter, Ollama, ...

For Groq:   base_url https://api.groq.com/openai/v1   api_key_env GROQ_API_KEY

Rate limits (free tiers are tight) are handled in three ways:
  1. Before sending, estimate the request size. If it can never fit in the
     per-minute limit, raise RequestTooLarge with a clear message.
  2. Keep a 60-second window of tokens used and wait if the next request
     would go over the per-minute limit.
  3. If the server still answers 429 "too many requests", wait the time it
     asks for and retry. If the DAILY limit is hit, raise DailyLimitReached
     so the controller can save the run for resuming later.
"""

from __future__ import annotations

import collections
import os
import time

import requests

from .base import DailyLimitReached, LLMError, LLMProvider, LLMReply, RequestTooLarge, estimate_tokens


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, cfg, sleep=time.sleep, clock=time.time):
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"
        self.api_key = os.environ.get(cfg.api_key_env, "")
        self.tokens_used = 0
        self._window = collections.deque()        # (timestamp, tokens) of recent requests
        self._dropped: set[str] = set()           # optional settings this model rejected (see complete)
        self._sleep, self._clock = sleep, clock
        if not self.api_key and "localhost" not in cfg.base_url and "127.0.0.1" not in cfg.base_url:
            raise LLMError(f"environment variable {cfg.api_key_env} is not set. "
                           f"Create an API key and run:  export {cfg.api_key_env}=<your key>")

    # -- rate limiting -------------------------------------------------------
    def _wait_for_minute_budget(self, needed: int) -> None:
        tpm = self.cfg.tokens_per_minute
        if not tpm:
            return
        if needed > tpm:
            raise RequestTooLarge(
                f"this request needs about {needed} tokens but your limit is {tpm} tokens per minute. "
                "Use a smaller design/fewer files, lower llm.max_output_tokens, or a plan with higher limits.")
        while True:
            now = self._clock()
            while self._window and now - self._window[0][0] > 60:
                self._window.popleft()
            used = sum(t for _, t in self._window)
            if used + needed <= tpm:
                return
            wait = 60 - (now - self._window[0][0]) + 1
            print(f"    [llm] waiting {wait:.0f}s to stay under {tpm} tokens/minute")
            self._sleep(wait)

    def _check_day_budget(self, needed: int) -> None:
        tpd = self.cfg.tokens_per_day
        if tpd and self.tokens_used + needed > tpd:
            raise DailyLimitReached(f"this run has used {self.tokens_used} tokens; the next request "
                                    f"(~{needed}) would pass the daily budget of {tpd}.")

    # -- the actual call -----------------------------------------------------
    def complete(self, messages: list[dict], max_tokens: int | None = None,
                 overrides: dict | None = None) -> LLMReply:
        max_tokens = max_tokens or self.cfg.max_output_tokens
        prompt_est = sum(estimate_tokens(m["content"]) for m in messages)
        needed = prompt_est + max_tokens
        self._check_day_budget(needed)
        body = {"model": self.cfg.model, "messages": messages, "temperature": self.cfg.temperature,
                "max_tokens": max_tokens, **self.cfg.extra_params, **(overrides or {})}
        # Optional settings a model may not support (e.g. reasoning_effort on a non-reasoning model).
        optional = {"temperature", *self.cfg.extra_params, *(overrides or {})}
        for key in self._dropped:
            body.pop(key, None)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error = ""
        for attempt in range(self.cfg.max_retries + 1):
            self._wait_for_minute_budget(needed)
            self._window.append((self._clock(), needed))
            try:
                resp = requests.post(self.url, json=body, headers=headers, timeout=self.cfg.request_timeout_s)
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                self._sleep(min(60, 5 * 2 ** attempt))
                continue

            if resp.status_code == 200:
                data = resp.json()
                text = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage") or {}
                reply = LLMReply(text=text,
                                 prompt_tokens=usage.get("prompt_tokens", prompt_est),
                                 completion_tokens=usage.get("completion_tokens", estimate_tokens(text)),
                                 model=data.get("model", self.cfg.model),
                                 cut_off=data["choices"][0].get("finish_reason") == "length")
                self._window[-1] = (self._window[-1][0], reply.total_tokens)   # replace estimate
                self.tokens_used += reply.total_tokens
                return reply

            detail = resp.text[:500]
            if resp.status_code == 429:
                low = detail.lower()
                if "per day" in low or "tokens per day" in low or "(tpd)" in low or "(rpd)" in low:
                    raise DailyLimitReached(f"the provider's daily limit was reached: {detail}")
                wait = float(resp.headers.get("retry-after", 0) or 0) or min(60, 5 * 2 ** attempt)
                print(f"    [llm] rate limited (429), waiting {wait:.0f}s")
                self._sleep(wait)
                last_error = detail
                continue
            if resp.status_code == 413 or "context_length" in detail or "too large" in detail.lower():
                raise RequestTooLarge(f"the provider says the request is too large: {detail}")
            if resp.status_code in (401, 403):
                raise LLMError(f"the API key was rejected ({resp.status_code}). Check {self.cfg.api_key_env}. "
                               f"Server said: {detail}")
            if resp.status_code in (400, 422):
                # The model rejected an optional setting by name: leave it out and ask again.
                rejected = [k for k in optional if k in body and k in detail]
                if rejected:
                    self._window.pop()                    # the rejected request used no tokens
                    for key in rejected:
                        body.pop(key)
                        self._dropped.add(key)
                    print(f"    [llm] model {self.cfg.model} does not accept {', '.join(rejected)}; "
                          "asking again without it")
                    last_error = detail
                    continue
            if resp.status_code >= 500:
                last_error = f"server error {resp.status_code}: {detail}"
                self._sleep(min(60, 5 * 2 ** attempt))
                continue
            raise LLMError(f"LLM request failed ({resp.status_code}): {detail}")
        raise LLMError(f"LLM request failed after {self.cfg.max_retries + 1} attempts: {last_error}")
