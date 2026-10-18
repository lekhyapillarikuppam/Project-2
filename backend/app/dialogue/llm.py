"""Groq client for speaker attribution (SPEC §15.3, D32, D34, D37): OpenAI-compatible chat
completions over `httpx`, a token-based rate limiter, a response cache and usage records.

Every failure ends in `LLMError` (or its subclass `LLMUnavailable` when the budget, the daily cap
or a too-long wait is the reason); the caller then uses the rule-based baseline for that window.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
REQUEST_TIMEOUT_SECONDS = 60.0
# A window that would wait longer than this for budget uses the baseline (SPEC §15.3).
MAX_WAIT_SECONDS = 120.0
BUDGET_WINDOW_SECONDS = 60.0
SECONDS_PER_DAY = 86_400
CHARS_PER_TOKEN = 4  # request size estimate: prompt chars / 4 + expected output
# Completion estimate before a request (D42): 12 tokens per quote id + 40; corrected from `usage`.
EXPECTED_OUTPUT_TOKENS_BASE = 40
EXPECTED_OUTPUT_TOKENS_PER_QUOTE = 12

# Reasoning settings per model family (verified with real requests, Phase 6.2 report):
# Qwen3 thinking is switched off; gpt-oss cannot switch it off, so it runs at the lowest effort.
REASONING_PARAMS: dict[str, dict[str, Any]] = {
    "qwen/qwen3": {"reasoning_effort": "none"},
    "openai/gpt-oss": {"reasoning_effort": "low"},
}


def reasoning_params(model: str) -> dict[str, Any]:
    return next((dict(v) for prefix, v in REASONING_PARAMS.items() if model.startswith(prefix)), {})


class LLMError(RuntimeError):
    """The request failed; the caller falls back to the baseline (D34)."""


class LLMUnavailable(LLMError):
    """Out of budget: daily cap reached or the wait for token budget is too long."""


def parse_reset(value: str | None) -> float | None:
    """Groq reset headers: `7.66s`, `2m59.56s`, `1h2m3s`, `120ms`, or plain seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    total = 0.0
    matched = False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value):
        matched = True
        total += float(number) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if matched else None


@dataclass
class TokenLimiter:
    """Two rolling 60 s budgets, all tokens and completion tokens (D42; 0 disables the second),
    plus a daily request cap; also obeys the server's rate-limit headers and `retry-after`.
    `clock` and `sleep` are injectable for tests."""

    tokens_per_minute: int
    max_requests_per_day: int
    output_tokens_per_minute: int = 0
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    max_wait: float = MAX_WAIT_SECONDS
    _spent: deque[list[float]] = field(default_factory=deque)  # [time, tokens, completion tokens]
    _blocked_until: float = 0.0
    _day: int = -1
    _requests_today: int = 0
    _server_day_exhausted_until: float = 0.0

    def _roll(self, now: float) -> None:
        while self._spent and self._spent[0][0] <= now - BUDGET_WINDOW_SECONDS:
            self._spent.popleft()
        day = int(now // SECONDS_PER_DAY)
        if day != self._day:
            self._day, self._requests_today = day, 0

    def _budget_wait(self, now: float, tokens: int, column: int, budget: int) -> float:
        """Seconds until `tokens` fit in one rolling budget (`column` of the spent entries); a
        request bigger than the whole budget may go once nothing else is in the window."""
        if budget <= 0 or not self._spent:
            return 0.0
        used = sum(entry[column] for entry in self._spent)
        if used + tokens <= budget:
            return 0.0
        for entry in self._spent:
            used -= entry[column]
            if used + tokens <= budget or used <= 0:
                return entry[0] + BUDGET_WINDOW_SECONDS - now
        return self._spent[-1][0] + BUDGET_WINDOW_SECONDS - now

    async def acquire(self, tokens: int, output_tokens: int = 0) -> None:
        """Wait for budget for a request of about `tokens` in total, `output_tokens` of them
        completion, then reserve it. Raises LLMUnavailable on the daily cap or when the wait
        would exceed `max_wait`."""
        waited = 0.0
        while True:
            now = self.clock()
            self._roll(now)
            if self._requests_today >= self.max_requests_per_day:
                raise LLMUnavailable(f"daily request cap ({self.max_requests_per_day}) reached")
            if now < self._server_day_exhausted_until:
                raise LLMUnavailable("the server's daily request limit is used up")
            wait = max(
                self._blocked_until - now,
                self._budget_wait(now, tokens, 1, self.tokens_per_minute),
                self._budget_wait(now, output_tokens, 2, self.output_tokens_per_minute),
                0.0,
            )
            if wait <= 0:
                self._spent.append([now, float(tokens), float(output_tokens)])
                self._requests_today += 1
                return
            if waited + wait > self.max_wait:
                raise LLMUnavailable(f"would wait {waited + wait:.0f} s for token budget")
            await self.sleep(wait)
            waited += wait

    def record(self, tokens: int, output_tokens: int | None = None) -> None:
        """Replace the estimates of the last reserved request with its real token counts."""
        if self._spent:
            self._spent[-1][1] = float(tokens)
            if output_tokens is not None:
                self._spent[-1][2] = float(output_tokens)

    def block_for(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self.clock() + seconds)

    def update_from_headers(self, headers: Mapping[str, str], next_tokens: int = 0) -> None:
        """`x-ratelimit-remaining-tokens` below what the next request needs blocks until
        `x-ratelimit-reset-tokens`; no remaining requests blocks until the requests reset."""
        remaining_tokens = headers.get("x-ratelimit-remaining-tokens")
        if remaining_tokens is not None and next_tokens:
            try:
                if float(remaining_tokens) < next_tokens:
                    self.block_for(parse_reset(headers.get("x-ratelimit-reset-tokens")) or BUDGET_WINDOW_SECONDS)
            except ValueError:
                pass
        if headers.get("x-ratelimit-remaining-requests", "").strip() == "0":
            reset = parse_reset(headers.get("x-ratelimit-reset-requests")) or SECONDS_PER_DAY
            self._server_day_exhausted_until = self.clock() + reset


def limiter_from_settings(settings: Settings) -> TokenLimiter:
    return TokenLimiter(
        settings.llm_tokens_per_minute,
        settings.llm_max_requests_per_day,
        output_tokens_per_minute=settings.llm_output_tokens_per_minute,
    )


@dataclass(frozen=True)
class UsageRecord:
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float
    cached: bool  # served from the cache (tokens and latency are those of the original request)


class GroqClient:
    """One chat-completion call per window: system + user prompt, temperature 0, JSON mode, the
    model's reasoning settings. Responses are cached in memory (per job) and, when `cache_dir` is
    given (evaluation), on disk, keyed by a hash of the request body."""

    def __init__(
        self,
        api_key: str,
        model: str,
        limiter: TokenLimiter,
        *,
        base_url: str = GROQ_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_dir: Path | None = None,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.limiter = limiter
        self.base_url = base_url
        self.transport = transport
        self.cache_dir = cache_dir
        self.timer = timer
        self.memory_cache: dict[str, dict[str, Any]] = {}
        self.usage: list[UsageRecord] = []

    def request_body(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **reasoning_params(self.model),
        }

    @staticmethod
    def cache_key(body: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _cached(self, key: str) -> dict[str, Any] | None:
        if key in self.memory_cache:
            return self.memory_cache[key]
        if self.cache_dir is not None:
            path = self.cache_dir / f"{key}.json"
            if path.exists():
                entry = json.loads(path.read_text(encoding="utf-8"))
                self.memory_cache[key] = entry
                return entry
        return None

    def _store(self, key: str, entry: dict[str, Any]) -> None:
        self.memory_cache[key] = entry
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def estimate_output_tokens(quotes: int) -> int:
        return EXPECTED_OUTPUT_TOKENS_BASE + EXPECTED_OUTPUT_TOKENS_PER_QUOTE * quotes

    @classmethod
    def estimate_tokens(cls, system: str, user: str, quotes: int) -> int:
        return (len(system) + len(user)) // CHARS_PER_TOKEN + cls.estimate_output_tokens(quotes)

    async def complete(self, system: str, user: str, quotes: int = 0) -> str:
        """The response content (a JSON string) for one request. Raises LLMError."""
        body = self.request_body(system, user)
        key = self.cache_key(body)
        cached = self._cached(key)
        if cached is not None:
            u = cached["usage"]
            self.usage.append(UsageRecord(self.model, u["prompt_tokens"], u["completion_tokens"], u["latency"], True))
            return str(cached["content"])

        estimate = self.estimate_tokens(system, user, quotes)
        output_estimate = self.estimate_output_tokens(quotes)
        async with httpx.AsyncClient(
            base_url=self.base_url, transport=self.transport, timeout=REQUEST_TIMEOUT_SECONDS
        ) as http:
            while True:
                await self.limiter.acquire(estimate, output_estimate)
                started = self.timer()
                try:
                    response = await http.post(
                        "/chat/completions", json=body, headers={"Authorization": f"Bearer {self.api_key}"}
                    )
                except httpx.HTTPError as e:
                    raise LLMError(f"request failed: {e!r}") from e
                latency = self.timer() - started
                self.limiter.update_from_headers(response.headers, estimate)
                if response.status_code == 429:
                    retry_after = parse_reset(response.headers.get("retry-after")) or BUDGET_WINDOW_SECONDS
                    logger.warning("Groq 429; retrying after %.1f s", retry_after)
                    self.limiter.record(0, 0)
                    self.limiter.block_for(retry_after)
                    continue  # acquire() raises LLMUnavailable once the wait gets too long
                if response.status_code != 200:
                    raise LLMError(f"HTTP {response.status_code}: {response.text[:200]}")
                try:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"] or ""
                    usage = data.get("usage") or {}
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    raise LLMError(f"unexpected response: {e!r}") from e
                prompt_tokens = int(usage.get("prompt_tokens", 0))
                completion_tokens = int(usage.get("completion_tokens", 0))
                self.limiter.record(prompt_tokens + completion_tokens or estimate, completion_tokens or output_estimate)
                self.usage.append(UsageRecord(self.model, prompt_tokens, completion_tokens, latency, False))
                self._store(key, {
                    "model": self.model, "content": content,
                    "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "latency": latency},
                })  # fmt: skip
                return str(content)


def client_from_settings(settings: Settings, cache_dir: Path | None = None) -> GroqClient | None:
    """The configured Groq client, or None when the method is `baseline` or no key is set."""
    if settings.attribution_method == "baseline" or not settings.groq_api_key:
        return None
    return GroqClient(settings.groq_api_key, settings.llm_model, limiter_from_settings(settings), cache_dir=cache_dir)
