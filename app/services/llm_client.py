import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(Exception):
    pass


@dataclass
class StageLogData:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    duration_ms: int
    error: str | None = None
    thinking: str | None = None


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = settings.model_pricing.get(model)
    if not pricing:
        return 0.0
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000


async def llm_complete(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stage_label: str = "llm",
    response_format: dict | None = None,
) -> tuple[str, StageLogData]:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://podcast-studio.local",
        "X-Title": "Research Podcast Studio",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)

    duration_ms = int((time.monotonic() - start) * 1000)

    if resp.status_code != 200:
        body = resp.text[:500]
        logger.error("LLM error [%s] %s: %s", model, resp.status_code, body)
        raise LLMError(f"OpenRouter {resp.status_code}: {body}")

    data = resp.json()
    message = data["choices"][0]["message"]
    raw_content = message.get("content") or message.get("reasoning_content") or ""

    # Extract <think> blocks from reasoning models (qwen3.5, etc.) — store separately
    thinking_blocks = re.findall(r"<think>(.*?)</think>", raw_content, flags=re.DOTALL)
    thinking = "\n\n---\n\n".join(b.strip() for b in thinking_blocks) or None
    content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cost = _estimate_cost(model, prompt_tokens, completion_tokens)

    log = StageLogData(
        stage=stage_label,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
        thinking=thinking,
    )
    logger.info(
        "LLM [%s] stage=%s tokens=%d+%d cost=$%.4f dur=%dms",
        model, stage_label, prompt_tokens, completion_tokens, cost, duration_ms,
    )
    return content, log


async def llm_stream(
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    stage_label: str = "llm",
) -> AsyncGenerator[str | dict, None]:
    """Async generator yielding token strings, then a final sentinel dict."""
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://podcast-studio.local",
        "X-Title": "Research Podcast Studio",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    start = time.monotonic()
    prompt_tokens = 0
    completion_tokens = 0

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", OPENROUTER_API_URL, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise LLMError(f"OpenRouter {resp.status_code}: {body[:500]}")

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content") or ""
                if token:
                    yield token

                # Capture usage from last chunk (OpenRouter sends it there)
                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

    duration_ms = int((time.monotonic() - start) * 1000)
    cost = _estimate_cost(model, prompt_tokens, completion_tokens)
    log = StageLogData(
        stage=stage_label,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
    )
    logger.info(
        "LLM stream [%s] stage=%s tokens=%d+%d cost=$%.4f dur=%dms",
        model, stage_label, prompt_tokens, completion_tokens, cost, duration_ms,
    )
    yield {"done": True, "log": log}
