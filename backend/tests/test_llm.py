"""Groq client, limiter, cache, windows and the LLM attribution loop (SPEC §15.3), offline:
`httpx.MockTransport` instead of the network and a fake clock instead of time."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.dialogue.attribute import Roster, baseline_attribute, llm_attribute
from app.dialogue.llm import GroqClient, LLMError, LLMUnavailable, TokenLimiter, parse_reset, reasoning_params
from app.dialogue.segment import find_quote_spans
from app.dialogue.windows import build_windows, collect_utterances, render_window


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def reply(content: object, status: int = 200, headers: dict[str, str] | None = None, tokens: int = 100) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}}],
        "usage": {"prompt_tokens": tokens, "completion_tokens": 10},
    }
    return httpx.Response(status, json=body, headers=headers or {})


class Server:
    """A scripted Groq: each request gets the next response (the last one repeats)."""

    def __init__(self, *responses: httpx.Response | Callable[[dict], httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        r = self.responses[min(len(self.requests), len(self.responses)) - 1]
        return r(body) if callable(r) else r


def client(server: Server, clock: FakeClock, *, tpm: int = 7000, per_day: int = 800, model: str = "qwen/qwen3.8-27b",
           cache_dir: Path | None = None) -> GroqClient:  # fmt: skip
    limiter = TokenLimiter(tpm, per_day, clock=clock, sleep=clock.sleep)
    return GroqClient("key", model, limiter, transport=httpx.MockTransport(server), cache_dir=cache_dir, timer=clock)


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


# --- limiter and client -------------------------------------------------------------------------


def test_parse_reset() -> None:
    assert parse_reset("7.66s") == pytest.approx(7.66)
    assert parse_reset("2m59.56s") == pytest.approx(179.56)
    assert parse_reset("120ms") == pytest.approx(0.12)
    assert parse_reset("1h2m3s") == pytest.approx(3723)
    assert parse_reset("12") == 12.0
    assert parse_reset(None) is None


def test_request_body_and_reasoning_settings() -> None:
    server, clock = Server(reply("{}")), FakeClock()
    run(client(server, clock).complete("sys", "user"))
    body = server.requests[0]
    assert body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "none"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert reasoning_params("openai/gpt-oss-120b") == {"reasoning_effort": "low"}
    assert reasoning_params("some/other-model") == {}


def test_rolling_token_budget_waits_for_the_oldest_request_to_expire() -> None:
    clock = FakeClock()
    limiter = TokenLimiter(7000, 800, clock=clock, sleep=clock.sleep)
    run(limiter.acquire(4000))
    clock.now += 10
    run(limiter.acquire(4000))  # 8000 > 7000: wait until the first leaves the 60 s window
    assert clock.sleeps == [pytest.approx(50)]


def test_estimate_is_replaced_by_the_real_token_count() -> None:
    clock = FakeClock()
    limiter = TokenLimiter(7000, 800, clock=clock, sleep=clock.sleep)
    run(limiter.acquire(6000))
    limiter.record(1000)
    run(limiter.acquire(5000))
    assert clock.sleeps == []


def test_a_wait_over_the_limit_raises_unavailable() -> None:
    clock = FakeClock()
    limiter = TokenLimiter(7000, 800, clock=clock, sleep=clock.sleep, max_wait=120)
    limiter.block_for(200)
    with pytest.raises(LLMUnavailable, match="would wait"):
        run(limiter.acquire(100))


def test_remaining_tokens_header_blocks_until_reset() -> None:
    clock = FakeClock()
    headers = {"x-ratelimit-remaining-tokens": "50", "x-ratelimit-reset-tokens": "7.5s"}
    server = Server(reply("{}", headers=headers), reply("{}"))
    c = client(server, clock)
    run(c.complete("sys", "first"))
    run(c.complete("sys", "second"))
    assert clock.sleeps == [pytest.approx(7.5)]


def test_429_waits_for_retry_after_then_succeeds() -> None:
    clock = FakeClock()
    server = Server(httpx.Response(429, headers={"retry-after": "5"}), reply('{"ok": 1}'))
    assert run(client(server, clock).complete("sys", "user")) == '{"ok": 1}'
    assert clock.sleeps == [pytest.approx(5)] and len(server.requests) == 2


def test_429_with_a_long_retry_after_gives_up() -> None:
    clock = FakeClock()
    server = Server(httpx.Response(429, headers={"retry-after": "300"}))
    with pytest.raises(LLMUnavailable):
        run(client(server, clock).complete("sys", "user"))


def test_daily_cap() -> None:
    clock = FakeClock()
    c = client(Server(reply("{}")), clock, per_day=1)
    run(c.complete("sys", "one"))
    with pytest.raises(LLMUnavailable, match="daily"):
        run(c.complete("sys", "two"))
    clock.now += 86_400  # the next day
    run(c.complete("sys", "three"))


def test_server_reporting_no_requests_left_today() -> None:
    clock = FakeClock()
    headers = {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2h"}
    c = client(Server(reply("{}", headers=headers)), clock)
    run(c.complete("sys", "one"))
    with pytest.raises(LLMUnavailable, match="daily"):
        run(c.complete("sys", "two"))


def test_http_error_raises_llm_error() -> None:
    with pytest.raises(LLMError, match="HTTP 500"):
        run(client(Server(httpx.Response(500, text="boom")), FakeClock()).complete("sys", "user"))


def test_cache_in_memory_and_on_disk(tmp_path: Path) -> None:
    clock = FakeClock()
    server = Server(reply('{"a": 1}', tokens=321))
    first = client(server, clock, cache_dir=tmp_path)
    run(first.complete("sys", "user"))
    run(first.complete("sys", "user"))
    second = client(server, clock, cache_dir=tmp_path)  # a new client, e.g. a re-run of the evaluation
    assert run(second.complete("sys", "user")) == '{"a": 1}'
    assert len(server.requests) == 1
    assert [u.cached for u in first.usage + second.usage] == [False, True, True]
    assert second.usage[0].prompt_tokens == 321  # the original request's figures


# --- windows --------------------------------------------------------------------------------------

PARAGRAPHS = [
    "It was a quiet afternoon in the garden and nobody spoke for a while.",
    "The Hatter opened his eyes very wide.",
    "‘Why is a raven,’ said the Hatter, ‘like a writing-desk?",  # continues into the next paragraph
    "‘It is a riddle, you know.’",
    "‘I believe I can guess that,’ said Alice aloud.",
    "‘Do you mean you think you can find out the answer?’ said the March Hare.",
    "‘Exactly so,’ said Alice.",
]


def _windows(max_words: int):  # noqa: ANN202
    spans, _ = find_quote_spans(PARAGRAPHS, "single")
    utterances = collect_utterances(spans)
    return spans, utterances, build_windows(PARAGRAPHS, utterances, max_words)


def test_windows_never_split_a_broken_or_multi_paragraph_quote() -> None:
    _, utterances, windows = _windows(max_words=8)
    for u in utterances:
        holders = [w for w in windows if any(p in w.paragraphs for p, _, _ in u.parts)]
        assert len(holders) == 1, u
    assert any({2, 3} <= set(w.paragraphs) for w in windows)  # the continued quote stays together


def test_context_paragraphs_carry_no_ids() -> None:
    _, utterances, windows = _windows(max_words=20)
    last = windows[-1]
    assert last.context
    text, labels = render_window(last, PARAGRAPHS, utterances)
    context, body = text.split("TEXT:\n")
    assert "[Q" not in context
    assert all(PARAGRAPHS[p] in context for p in last.context)
    assert "[Q1]‘" in body and set(labels) == {f"Q{n}" for n in range(1, len(last.utterance_ids) + 1)}


def test_broken_quote_has_one_id_on_both_pieces() -> None:
    _, utterances, windows = _windows(max_words=1000)
    text, labels = render_window(windows[0], PARAGRAPHS, utterances)
    assert text.count("[Q1]") == 3  # two pieces in ¶2 and the continuation in ¶3
    assert len(windows) == 1 and "CONTEXT" not in text


# --- the attribution loop -------------------------------------------------------------------------


def _answer(body: dict, speaker: str = "Alice", skip: int = 0) -> httpx.Response:
    user = body["messages"][1]["content"]
    ids = sorted({part.split("]")[0] for part in user.split("[Q")[1:]}, key=int)
    q = {f"Q{i}": speaker for i in ids[skip:]}
    return reply({"q": q, "new": [{"name": speaker, "gender": "female", "aliases": []}]})


def _run_llm(server: Server, max_words: int = 1000):  # noqa: ANN202
    spans, _ = find_quote_spans(PARAGRAPHS, "single")
    baseline = baseline_attribute(PARAGRAPHS, spans)
    roster = Roster()
    c = client(server, FakeClock())
    result = run(llm_attribute(PARAGRAPHS, spans, c, roster, baseline, max_words))
    return result, baseline, roster


def test_valid_reply_is_used() -> None:
    result, _, roster = _run_llm(Server(lambda b: _answer(b, "Alice")))
    assert {a.speaker for a in result.attributions.values()} == {"Alice"}
    assert {a.reason for a in result.attributions.values()} == {"llm"}
    assert result.method_used == "llm" and roster.find("Alice") is not None


def test_invalid_reply_is_retried_once_with_the_error() -> None:
    server = Server(lambda b: _answer(b, skip=1), lambda b: _answer(b))
    result, _, _ = _run_llm(server)
    assert len(server.requests) == 2
    assert "previous reply was invalid: missing ids: Q1" in server.requests[1]["messages"][1]["content"]
    assert result.method_used == "llm"


def test_two_invalid_replies_fall_back_to_the_baseline() -> None:
    server = Server(lambda b: _answer(b, skip=1))
    result, baseline, _ = _run_llm(server)
    assert len(server.requests) == 2
    assert result.attributions == baseline.attributions and result.method_used == "baseline"


def test_failed_request_falls_back_to_the_baseline_for_that_window_only() -> None:
    calls = {"n": 0}

    def flaky(body: dict) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] == 1 else _answer(body, "Alice")

    result, baseline, _ = _run_llm(Server(flaky), max_words=20)
    assert result.windows_total > 1 and result.method_used == "mixed"
    reasons = {a.method for a in result.attributions.values()}
    assert reasons == {"baseline", "llm"}
