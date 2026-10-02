"""The only file that talks to the model provider.

One function in, one validated pydantic object out, usage recorded. Model ids
come from the environment (`DIGEST_MODEL`, `DIGEST_MODEL_BULK`); prices come
from `prices.json`. Cost is computed here, in code, from the usage the API
reports for each call.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings

T = TypeVar("T", bound=BaseModel)

PRICES = json.loads((Path(__file__).parent / "prices.json").read_text())
MILLION = Decimal(1_000_000)


class LLMError(RuntimeError):
    """The model call failed, was refused, was cut off, or did not match the schema."""


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None  # None when prices.json has no entry for the model
    seconds: float = 0.0


def cost_usd(model: str, input_tokens: int, cached: int, cache_write: int, output_tokens: int) -> float | None:
    """Price one call. Cached and cache-write tokens are part of input_tokens and
    are billed at their own rates; reasoning tokens are already inside output_tokens."""
    price = PRICES["models"].get(model)
    if price is None:
        return None
    rate_in = Decimal(str(price["input"]))
    rate_cached = Decimal(str(price["cached_input"] if price.get("cached_input") is not None else price["input"]))
    rate_write = Decimal(str(price["cache_write"] if price.get("cache_write") is not None else price["input"]))
    threshold = price.get("long_context_over_input_tokens")
    if threshold and input_tokens > threshold:
        multiplier = Decimal(str(price.get("long_context_input_multiplier", 1)))
        rate_in, rate_cached, rate_write = rate_in * multiplier, rate_cached * multiplier, rate_write * multiplier
    fresh = max(input_tokens - cached - cache_write, 0)
    total = (
        fresh * rate_in + cached * rate_cached + cache_write * rate_write + output_tokens * Decimal(str(price["output"]))
    ) / MILLION
    return float(total.quantize(Decimal("0.000001")))


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def image_part(jpeg: bytes, detail: str = "high") -> dict:
    encoded = base64.b64encode(jpeg).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}", "detail": detail}


class _Throttle:
    """Shared by all worker threads: when the provider says the per-minute budget
    is nearly spent, everyone waits for the reset instead of collecting 429s."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resume_at = 0.0

    def wait(self) -> None:
        with self._lock:
            pause = self._resume_at - time.monotonic()
        if pause > 0:
            time.sleep(pause)

    def observe(self, headers, reserve_tokens: int) -> None:
        try:
            remaining = int(headers.get("x-ratelimit-remaining-tokens", ""))
        except ValueError:
            return
        if remaining < reserve_tokens:
            with self._lock:
                self._resume_at = max(self._resume_at, time.monotonic() + _seconds(headers.get("x-ratelimit-reset-tokens")))


def _seconds(value: str | None) -> float:
    """Reset headers read like '6m0s', '1.5s' or '250ms'."""
    if not value:
        return 5.0
    total, number = 0.0, ""
    units = {"h": 3600.0, "m": 60.0, "s": 1.0}
    i = 0
    while i < len(value):
        ch = value[i]
        if ch.isdigit() or ch == ".":
            number += ch
        elif value.startswith("ms", i):
            total += float(number or 0) / 1000.0
            number, i = "", i + 1
        elif ch in units:
            total += float(number or 0) * units[ch]
            number = ""
        i += 1
    return min(total or 5.0, 90.0)


_throttle = _Throttle()
_client = None
_client_lock = threading.Lock()


def _get_client(cfg: Settings):
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI

            if not cfg.openai_api_key:
                raise LLMError("OPENAI_API_KEY is not set")
            _client = OpenAI(api_key=cfg.openai_api_key, max_retries=5, timeout=300.0)
        return _client


def structured(
    cfg: Settings,
    *,
    model: str,
    instructions: str,
    content: list[dict],
    schema: type[T],
    effort: str = "low",
    max_output_tokens: int = 16000,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> tuple[T, Usage]:
    """Send one request and return (validated object, usage). Raises LLMError.

    `timeout` (seconds) and `max_retries` override the client's patient defaults for
    an interactive caller that would rather fail fast than wait through backoffs."""
    from openai import OpenAIError
    from openai.lib._pydantic import to_strict_json_schema

    if not model:
        raise LLMError("DIGEST_MODEL is not set")
    client = _get_client(cfg)
    if timeout is not None or max_retries is not None:
        options = {key: value for key, value in (("timeout", timeout), ("max_retries", max_retries)) if value is not None}
        client = client.with_options(**options)
    else:
        _throttle.wait()  # a batch call queues behind the rate-limit pause; an interactive one does not
    started = time.monotonic()
    try:
        raw = client.responses.with_raw_response.create(
            model=model,
            instructions=instructions,
            input=[{"role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "strict": True,
                    "schema": to_strict_json_schema(schema),
                }
            },
            reasoning={"effort": effort},
            max_output_tokens=max_output_tokens,
            store=False,  # nothing from the case file is retained for later retrieval
            # The provider otherwise writes every prompt to its cache and bills the write above the plain
            # rate; one-shot digest prompts are never read back. A caller that does reuse a block marks it.
            prompt_cache_options={"mode": "explicit"},
        )
    except OpenAIError as error:
        # The provider's message may echo input; keep only the error type.
        raise LLMError(f"model call failed: {type(error).__name__}") from error
    _throttle.observe(raw.headers, reserve_tokens=max_output_tokens * 2)
    response = raw.parse()

    reported = response.usage
    usage = Usage(model=model, seconds=round(time.monotonic() - started, 2))
    if reported is not None:
        details_in, details_out = reported.input_tokens_details, reported.output_tokens_details
        usage.input_tokens = reported.input_tokens or 0
        usage.output_tokens = reported.output_tokens or 0
        usage.cached_tokens = getattr(details_in, "cached_tokens", 0) or 0
        usage.cache_write_tokens = getattr(details_in, "cache_write_tokens", 0) or 0
        usage.reasoning_tokens = getattr(details_out, "reasoning_tokens", 0) or 0
        usage.cost_usd = cost_usd(
            model, usage.input_tokens, usage.cached_tokens, usage.cache_write_tokens, usage.output_tokens
        )

    text_out = ""
    for item in response.output or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                raise LLMUsageError("the model declined this request", usage)
            if getattr(part, "type", "") == "output_text":
                text_out += part.text
    if response.status != "completed":
        reason = getattr(response.incomplete_details, "reason", None) or response.status
        raise LLMUsageError(f"response incomplete: {reason}", usage)
    try:
        return schema.model_validate_json(text_out), usage
    except ValidationError as error:
        raise LLMUsageError(f"output did not match the {schema.__name__} schema ({error.error_count()} errors)", usage) from error


class LLMUsageError(LLMError):
    """A failed call that was still billed: carries its usage so the cost is counted."""

    def __init__(self, message: str, usage: Usage):
        super().__init__(message)
        self.usage = usage
