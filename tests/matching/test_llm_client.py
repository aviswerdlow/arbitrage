"""Tests for LLM client with DeepSeek and GPT-4o support."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arbitrage.matching.llm_client import LLMClient, RateLimiter


@pytest.fixture
def mock_deepseek_response():
    """Mock DeepSeek API response."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "similarity": 0.95,
                        "explanation": "Markets are equivalent",
                        "field_matches": {
                            "time_window": True,
                            "outcome_definition": True,
                            "resolution_source": True,
                        },
                    })
                }
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
    }


@pytest.fixture
def mock_openai_response():
    """Mock OpenAI API response."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "similarity": 0.92,
                        "explanation": "Markets are similar",
                        "field_matches": {
                            "time_window": True,
                            "outcome_definition": True,
                            "resolution_source": False,
                        },
                    })
                }
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "total_tokens": 180,
        },
    }


class TestRateLimiter:
    """Tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_requests(self):
        """Rate limiter allows requests within limits."""
        limiter = RateLimiter(max_requests=5, window_seconds=1.0)

        # Should allow 5 requests immediately
        for _ in range(5):
            await limiter.acquire()

        assert len(limiter._requests) == 5

    @pytest.mark.asyncio
    async def test_rate_limiter_clears_old_requests(self):
        """Rate limiter clears old requests outside window."""
        limiter = RateLimiter(max_requests=2, window_seconds=0.1)

        await limiter.acquire()
        await limiter.acquire()

        # Wait for window to expire
        import asyncio
        await asyncio.sleep(0.15)

        await limiter.acquire()
        # Old requests should be cleared
        assert len(limiter._requests) == 1


class TestLLMClient:
    """Tests for LLM client."""

    def test_initialization(self):
        """Client initializes with correct defaults."""
        client = LLMClient(
            deepseek_api_key="test_key",
            primary_provider="deepseek",
        )

        assert client.deepseek_api_key == "test_key"
        assert client.primary_provider == "deepseek"
        assert client.enable_fallback is True

    def test_estimate_tokens(self):
        """Client estimates token count."""
        client = LLMClient(deepseek_api_key="test")

        # Rough estimate without tiktoken
        tokens = client.estimate_tokens("This is a test message")
        assert tokens > 0

    def test_calculate_cost_deepseek(self):
        """Client calculates DeepSeek costs correctly."""
        client = LLMClient(deepseek_api_key="test")

        cost = client.calculate_cost(
            provider="deepseek",
            model="deepseek-chat",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )

        # Should be input cost + output cost
        expected = client.DEEPSEEK_COST_PER_1M_INPUT + client.DEEPSEEK_COST_PER_1M_OUTPUT
        assert cost == pytest.approx(expected, rel=0.01)

    def test_calculate_cost_openai(self):
        """Client calculates OpenAI costs correctly."""
        client = LLMClient(openai_api_key="test")

        cost = client.calculate_cost(
            provider="openai",
            model="gpt-4o",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )

        expected = client.GPT4O_COST_PER_1M_INPUT + client.GPT4O_COST_PER_1M_OUTPUT
        assert cost == pytest.approx(expected, rel=0.01)

    @pytest.mark.asyncio
    async def test_call_api_deepseek_success(self, mock_deepseek_response):
        """Client successfully calls DeepSeek API."""
        client = LLMClient(deepseek_api_key="test_key")

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_deepseek_response
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            messages = [{"role": "user", "content": "Test"}]
            result = await client._call_api("deepseek", messages)

            assert result == mock_deepseek_response
            assert len(client.usage_history) == 1
            assert client.usage_history[0].provider == "deepseek"
            assert client.usage_history[0].prompt_tokens == 100

    @pytest.mark.asyncio
    async def test_complete_with_fallback(self, mock_deepseek_response, mock_openai_response):
        """Client falls back to OpenAI when DeepSeek fails."""
        client = LLMClient(
            deepseek_api_key="test_key",
            openai_api_key="fallback_key",
            primary_provider="deepseek",
            enable_fallback=True,
        )

        messages = [{"role": "user", "content": "Test"}]

        with patch("httpx.AsyncClient.post") as mock_post:
            # First call (DeepSeek) fails
            mock_post.side_effect = [
                Exception("DeepSeek API error"),
                # Second call (OpenAI) succeeds
                MagicMock(
                    json=lambda: mock_openai_response,
                    raise_for_status=lambda: None,
                ),
            ]

            result = await client.complete(messages)

            # Should get OpenAI response after fallback
            assert result["similarity"] == 0.92
            assert len(client.usage_history) == 1
            assert client.usage_history[0].provider == "openai"

    @pytest.mark.asyncio
    async def test_complete_no_fallback_raises(self):
        """Client raises exception when fallback disabled."""
        client = LLMClient(
            deepseek_api_key="test_key",
            primary_provider="deepseek",
            enable_fallback=False,
        )

        messages = [{"role": "user", "content": "Test"}]

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.side_effect = Exception("API error")

            with pytest.raises(Exception, match="API error"):
                await client.complete(messages)

    def test_get_total_cost(self):
        """Client tracks total cost across calls."""
        client = LLMClient(deepseek_api_key="test")

        # Manually add usage
        from arbitrage.matching.llm_client import LLMUsage
        from datetime import datetime

        client.usage_history.append(
            LLMUsage(
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.001,
                timestamp=datetime.now(),
            )
        )
        client.usage_history.append(
            LLMUsage(
                provider="openai",
                model="gpt-4o",
                prompt_tokens=200,
                completion_tokens=100,
                total_tokens=300,
                cost_usd=0.002,
                timestamp=datetime.now(),
            )
        )

        assert client.get_total_cost() == 0.003

    def test_get_usage_summary(self):
        """Client provides usage summary."""
        client = LLMClient(deepseek_api_key="test")

        from arbitrage.matching.llm_client import LLMUsage
        from datetime import datetime

        client.usage_history.append(
            LLMUsage(
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.001,
                timestamp=datetime.now(),
            )
        )

        summary = client.get_usage_summary()

        assert summary["total_calls"] == 1
        assert summary["deepseek_calls"] == 1
        assert summary["openai_calls"] == 0
        assert summary["total_tokens"] == 150
        assert summary["total_cost_usd"] == 0.001
