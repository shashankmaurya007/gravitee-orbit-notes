"""HTTP client for the Gemini proxy via Gravitee."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_CHAT_URL = f"{settings.gemini_base_url}/chat/completions"
_KEYS = [k for k in [settings.gemini_api_key, settings.gemini_api_key_2] if k]

_MAX_RETRIES = 8
_RETRY_BASE = 3.0
_SEMAPHORE = asyncio.Semaphore(1)
_MIN_INTERVAL = 2.0
_last_request_time: float = 0.0


async def chat(
    system: str,
    user: str,
    temperature: float = 0.2,
    expect_json: bool = False,
) -> str:
    """Single-turn chat call with exponential backoff on 429."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if expect_json:
        messages[0]["content"] += "\n\nRespond with valid JSON only. No markdown, no code fences."

    payload = {
        "model": settings.gemini_model,
        "messages": messages,
        "temperature": temperature,
    }

    global _last_request_time
    import time

    async with _SEMAPHORE:
        # Enforce minimum interval between requests
        now = time.monotonic()
        gap = _MIN_INTERVAL - (now - _last_request_time)
        if gap > 0:
            await asyncio.sleep(gap)
        _last_request_time = time.monotonic()

        return await _chat_with_retry(payload, expect_json)


async def _chat_with_retry(payload: dict, expect_json: bool) -> str:
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        # Rotate keys on each attempt: key1, key2, key1, key2 …
        api_key = _KEYS[attempt % len(_KEYS)]
        headers = {"X-Gravitee-Api-Key": api_key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(_CHAT_URL, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = _RETRY_BASE ** (attempt // len(_KEYS))
                    logger.warning("429 key%d — retry %d/%d in %.1fs",
                                   (attempt % len(_KEYS)) + 1, attempt + 1, _MAX_RETRIES, wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()

            text = (data["choices"][0]["message"]["content"] or "").strip()
            if expect_json and text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()
            return text

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                wait = _RETRY_BASE ** (attempt // len(_KEYS))
                logger.warning("429 key%d — retry %d/%d in %.1fs",
                               (attempt % len(_KEYS)) + 1, attempt + 1, _MAX_RETRIES, wait)
                await asyncio.sleep(wait)
                last_exc = exc
                continue
            raise

    raise RuntimeError(f"Exceeded {_MAX_RETRIES} retries across {len(_KEYS)} key(s)") from last_exc
