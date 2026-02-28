"""OpenRouter API client for making LLM requests."""

import logging
from time import perf_counter
from typing import Any, Dict, List, Optional

import httpx

from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL

logger = logging.getLogger("uvicorn.error")


def _extract_error_message(response: httpx.Response) -> str:
    """Extract a concise error message from an HTTP response."""
    try:
        data = response.json()
    except Exception:
        return response.text[:300].replace("\n", " ")

    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(error or data)


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    prompt_chars = sum(len(msg.get("content", "")) for msg in messages)
    start = perf_counter()
    logger.info(
        "openrouter.request model=%s messages=%d prompt_chars=%d timeout_s=%.1f",
        model,
        len(messages),
        prompt_chars,
        timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
            )

        elapsed = perf_counter() - start
        if response.status_code >= 400:
            logger.warning(
                "openrouter.response model=%s status=%d elapsed_s=%.2f error=%s",
                model,
                response.status_code,
                elapsed,
                _extract_error_message(response),
            )
            return None

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            logger.warning(
                "openrouter.response model=%s status=%d elapsed_s=%.2f error=no choices in response",
                model,
                response.status_code,
                elapsed,
            )
            return None

        message = choices[0].get("message", {})
        usage = data.get("usage", {})
        completion_tokens = usage.get("completion_tokens", "n/a")
        total_tokens = usage.get("total_tokens", "n/a")
        finish_reason = choices[0].get("finish_reason", "unknown")
        content = message.get("content")
        reasoning_details = message.get("reasoning_details")

        logger.info(
            "openrouter.response model=%s status=%d elapsed_s=%.2f finish_reason=%s completion_tokens=%s total_tokens=%s content_chars=%d",
            model,
            response.status_code,
            elapsed,
            finish_reason,
            completion_tokens,
            total_tokens,
            len(content or ""),
        )

        return {
            "content": content,
            "reasoning_details": reasoning_details,
        }

    except Exception as e:
        elapsed = perf_counter() - start
        logger.exception(
            "openrouter.exception model=%s elapsed_s=%.2f error=%s",
            model,
            elapsed,
            str(e),
        )
        return None


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    start = perf_counter()
    logger.info(
        "openrouter.parallel.start model_count=%d models=%s",
        len(models),
        ",".join(models),
    )

    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    elapsed = perf_counter() - start
    success_count = sum(1 for response in responses if response is not None)
    logger.info(
        "openrouter.parallel.done model_count=%d success=%d failed=%d elapsed_s=%.2f",
        len(models),
        success_count,
        len(models) - success_count,
        elapsed,
    )

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
