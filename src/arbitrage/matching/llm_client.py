"""LLM client with DeepSeek primary and GPT-4o fallback support."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx
import tiktoken
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = get_logger(__name__)


@dataclass
class LLMUsage:
    """Token usage and cost tracking for LLM calls."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    timestamp: datetime


@dataclass
class RateLimiter:
    """Simple token bucket rate limiter."""

    max_requests: int
    window_seconds: float
    _requests: list[datetime] = None

    def __post_init__(self):
        if self._requests is None:
            self._requests = []

    async def acquire(self) -> None:
        """Wait until a request can be made within rate limits."""
        while True:
            now = datetime.now()
            cutoff = now - timedelta(seconds=self.window_seconds)

            # Remove requests outside the window
            self._requests = [req for req in self._requests if req > cutoff]

            if len(self._requests) < self.max_requests:
                self._requests.append(now)
                return

            oldest = self._requests[0]
            wait_time = (oldest - cutoff).total_seconds()
            if wait_time > 0:
                logger.debug("rate_limit_waiting", wait_seconds=wait_time)
                await asyncio.sleep(wait_time)
            else:
                # Window advanced enough; retry with fresh timestamp.
                await asyncio.sleep(0)


class LLMClient:
    """Unified LLM client with DeepSeek primary and GPT-4o fallback.

    Implements:
    - DeepSeek API integration (60 req/min limit)
    - GPT-4o fallback on DeepSeek failure
    - Automatic retry with exponential backoff
    - Token usage and cost tracking
    - Rate limiting
    """

    DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
    OPENAI_URL = "https://api.openai.com/v1/chat/completions"

    # Pricing (as of 2024, per 1M tokens)
    DEEPSEEK_COST_PER_1M_INPUT = 0.14
    DEEPSEEK_COST_PER_1M_OUTPUT = 0.28
    GPT4O_COST_PER_1M_INPUT = 2.50
    GPT4O_COST_PER_1M_OUTPUT = 10.00

    def __init__(
        self,
        deepseek_api_key: str | None = None,
        openai_api_key: str | None = None,
        primary_provider: Literal["deepseek", "openai"] = "deepseek",
        enable_fallback: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        """Initialize LLM client.

        Args:
            deepseek_api_key: DeepSeek API key
            openai_api_key: OpenAI API key (for GPT-4o fallback)
            primary_provider: Primary LLM provider to use
            enable_fallback: Enable fallback to secondary provider on failure
            timeout_seconds: Request timeout
        """
        self.deepseek_api_key = deepseek_api_key
        self.openai_api_key = openai_api_key
        self.primary_provider = primary_provider
        self.enable_fallback = enable_fallback
        self.timeout = timeout_seconds

        # Rate limiters per TDD requirements
        self.deepseek_limiter = RateLimiter(max_requests=60, window_seconds=60)
        self.openai_limiter = RateLimiter(max_requests=500, window_seconds=60)

        # Token encoder for cost estimation
        self._encoder = None

        # Usage tracking
        self.usage_history: list[LLMUsage] = []

    def _get_encoder(self):
        """Lazy load tiktoken encoder."""
        if self._encoder is None:
            try:
                self._encoder = tiktoken.get_encoding("cl100k_base")
            except Exception as exc:
                logger.warning("tiktoken_load_failed", error=str(exc))
                # Fallback: rough estimate of 4 chars per token
                self._encoder = None
        return self._encoder

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text.

        Args:
            text: Input text

        Returns:
            Estimated token count
        """
        encoder = self._get_encoder()
        if encoder:
            return len(encoder.encode(text))
        else:
            # Rough estimate: 4 characters per token
            return len(text) // 4

    def calculate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calculate cost for LLM call.

        Args:
            provider: Provider name ("deepseek" or "openai")
            model: Model name
            prompt_tokens: Input token count
            completion_tokens: Output token count

        Returns:
            Cost in USD
        """
        if provider == "deepseek":
            input_cost = (prompt_tokens / 1_000_000) * self.DEEPSEEK_COST_PER_1M_INPUT
            output_cost = (completion_tokens / 1_000_000) * self.DEEPSEEK_COST_PER_1M_OUTPUT
        else:  # openai
            input_cost = (prompt_tokens / 1_000_000) * self.GPT4O_COST_PER_1M_INPUT
            output_cost = (completion_tokens / 1_000_000) * self.GPT4O_COST_PER_1M_OUTPUT

        return input_cost + output_cost

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _call_api(
        self,
        provider: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> dict:
        """Make API call to LLM provider with retry logic.

        Args:
            provider: Provider to call ("deepseek" or "openai")
            messages: Chat messages
            temperature: Sampling temperature
            max_tokens: Maximum completion tokens

        Returns:
            API response dictionary

        Raises:
            httpx.HTTPError: On API failure
        """
        # Apply rate limiting
        if provider == "deepseek":
            await self.deepseek_limiter.acquire()
            url = self.DEEPSEEK_URL
            api_key = self.deepseek_api_key
            model = "deepseek-chat"
        else:
            await self.openai_limiter.acquire()
            url = self.OPENAI_URL
            api_key = self.openai_api_key
            model = "gpt-4o"

        if not api_key:
            raise ValueError(f"No API key configured for {provider}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},  # Force JSON output
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            logger.debug(
                "llm_api_call",
                provider=provider,
                model=model,
                message_count=len(messages),
            )

            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

            data = response.json()

            # Track usage
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

            cost = self.calculate_cost(provider, model, prompt_tokens, completion_tokens)

            self.usage_history.append(
                LLMUsage(
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    timestamp=datetime.now(),
                )
            )

            logger.info(
                "llm_api_success",
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=round(cost, 6),
            )

            return data

    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 1000,
    ) -> dict:
        """Call LLM with automatic fallback.

        Args:
            messages: Chat messages in OpenAI format
            temperature: Sampling temperature (0.0 = deterministic)
            max_tokens: Maximum completion tokens

        Returns:
            Parsed JSON response from LLM

        Raises:
            Exception: If both primary and fallback fail
        """
        try:
            # Try primary provider
            response = await self._call_api(
                self.primary_provider,
                messages,
                temperature,
                max_tokens,
            )

            content = response["choices"][0]["message"]["content"]
            return json.loads(content)

        except Exception as primary_exc:
            logger.warning(
                "primary_llm_failed",
                provider=self.primary_provider,
                error=str(primary_exc),
            )

            if not self.enable_fallback:
                raise

            # Try fallback provider
            fallback = "openai" if self.primary_provider == "deepseek" else "deepseek"

            try:
                logger.info("attempting_fallback", fallback_provider=fallback)

                response = await self._call_api(
                    fallback,
                    messages,
                    temperature,
                    max_tokens,
                )

                content = response["choices"][0]["message"]["content"]
                return json.loads(content)

            except Exception as fallback_exc:
                logger.error(
                    "fallback_llm_failed",
                    fallback_provider=fallback,
                    error=str(fallback_exc),
                )
                # Re-raise primary exception since that's more relevant
                raise primary_exc from fallback_exc

    def get_total_cost(self) -> float:
        """Get total cost of all LLM calls.

        Returns:
            Total cost in USD
        """
        return sum(usage.cost_usd for usage in self.usage_history)

    def get_usage_summary(self) -> dict:
        """Get summary of LLM usage.

        Returns:
            Dictionary with usage statistics
        """
        deepseek_calls = [u for u in self.usage_history if u.provider == "deepseek"]
        openai_calls = [u for u in self.usage_history if u.provider == "openai"]

        return {
            "total_calls": len(self.usage_history),
            "deepseek_calls": len(deepseek_calls),
            "openai_calls": len(openai_calls),
            "total_tokens": sum(u.total_tokens for u in self.usage_history),
            "total_cost_usd": round(self.get_total_cost(), 4),
            "deepseek_cost_usd": round(sum(u.cost_usd for u in deepseek_calls), 4),
            "openai_cost_usd": round(sum(u.cost_usd for u in openai_calls), 4),
        }


__all__ = ["LLMClient", "LLMUsage", "RateLimiter"]
