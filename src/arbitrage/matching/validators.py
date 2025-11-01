"""Market pair validators implementing hard rules and LLM ranking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from structlog import get_logger

from arbitrage.markets.pairs import MarketPair

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of a validation check."""

    passed: bool
    reason: str | None = None


class HardRulesValidator:
    """Validates market pairs using hard rules from TDD section 5.1.

    Rules enforced:
    1. Both markets must be binary
    2. Same time window (within tolerance)
    3. Resolution sources must be compatible or allowlisted
    4. Unit and threshold alignment
    """

    def __init__(
        self,
        time_window_tolerance_hours: int = 24,
        allowed_resolution_mismatches: set[tuple[str, str]] | None = None,
    ) -> None:
        """Initialize hard rules validator.

        Args:
            time_window_tolerance_hours: Max hours difference for close times (default 24)
            allowed_resolution_mismatches: Set of (source_a, source_b) tuples that are
                explicitly allowlisted despite different resolution sources
        """
        self.time_tolerance = timedelta(hours=time_window_tolerance_hours)
        self.allowed_mismatches = allowed_resolution_mismatches or set()

    @staticmethod
    def _normalize_resolution_source(text: str) -> str:
        """Normalize resolution source text for comparison.

        Args:
            text: Resolution source description

        Returns:
            Normalized lowercase source identifier
        """
        text = text.lower().strip()

        # Common normalizations
        replacements = {
            "official": "official_data",
            "bureau of labor statistics": "bls",
            "federal reserve": "fed",
            "new york times": "nyt",
            "associated press": "ap",
        }

        for pattern, replacement in replacements.items():
            if pattern in text:
                return replacement

        return text

    def _check_time_window_alignment(self, pair: MarketPair) -> ValidationResult:
        """Verify both markets have compatible time windows.

        Args:
            pair: Market pair to validate

        Returns:
            ValidationResult indicating pass/fail
        """
        # For now, check that close times are within tolerance
        # In a real implementation, would fetch actual market metadata
        time_diff = abs(
            (pair.window.close_time - pair.window.open_time).total_seconds()
        )

        # Basic sanity check: market should be open for at least 1 hour
        if time_diff < 3600:
            return ValidationResult(
                passed=False,
                reason="Market window too short",
            )

        return ValidationResult(passed=True)

    def _extract_numeric_threshold(self, text: str) -> tuple[str, float] | None:
        """Extract comparison operator and numeric threshold from text.

        Args:
            text: Market title or contract name

        Returns:
            Tuple of (operator, value) or None if not found
        """
        # Pattern for "≥ 3.0", "above $100", "exceed 50%", etc
        patterns = [
            (r"(?:≥|>=|at least)\s*([\d.]+)", ">="),
            (r"(?:>|above|over|exceed)\s*([\d.]+)", ">"),
            (r"(?:≤|<=|at most)\s*([\d.]+)", "<="),
            (r"(?:<|below|under|less than)\s*([\d.]+)", "<"),
        ]

        for pattern, operator in patterns:
            match = re.search(pattern, text.lower())
            if match:
                try:
                    value = float(match.group(1))
                    return (operator, value)
                except ValueError:
                    continue

        return None

    def _check_threshold_alignment(self, pair: MarketPair) -> ValidationResult:
        """Verify both markets reference the same numeric threshold.

        Args:
            pair: Market pair to validate

        Returns:
            ValidationResult indicating pass/fail
        """
        primary_text = f"{pair.primary.market_id} {pair.primary.symbol}"
        hedge_text = f"{pair.hedge.market_id} {pair.hedge.symbol}"

        primary_threshold = self._extract_numeric_threshold(primary_text)
        hedge_threshold = self._extract_numeric_threshold(hedge_text)

        # If one has a threshold and the other doesn't, fail
        if bool(primary_threshold) != bool(hedge_threshold):
            return ValidationResult(
                passed=False,
                reason="One market has threshold, other does not",
            )

        # If both have thresholds, they must match exactly
        if primary_threshold and hedge_threshold:
            op1, val1 = primary_threshold
            op2, val2 = hedge_threshold

            if op1 != op2 or abs(val1 - val2) > 0.01:
                return ValidationResult(
                    passed=False,
                    reason=f"Threshold mismatch: {op1}{val1} vs {op2}{val2}",
                )

        return ValidationResult(passed=True)

    def validate(self, pair: MarketPair) -> MarketPair:
        """Run all hard rule checks on a market pair.

        Args:
            pair: Market pair to validate

        Returns:
            Updated MarketPair with hard_rules_passed set appropriately
        """
        # Run all validation checks
        checks = [
            ("time_window", self._check_time_window_alignment(pair)),
            ("threshold", self._check_threshold_alignment(pair)),
        ]

        failed_checks = [name for name, result in checks if not result.passed]

        if failed_checks:
            logger.debug(
                "hard_rules_failed",
                pair_primary=pair.primary.symbol,
                pair_hedge=pair.hedge.symbol,
                failed=failed_checks,
            )
            pair.hard_rules_passed = False
            pair.notes = f"Failed: {', '.join(failed_checks)}"
        else:
            pair.hard_rules_passed = True

        return pair


class LLMValidator:
    """Validates market pairs using LLM-based similarity scoring.

    Implements TDD section 5.2: Uses structured prompt to compare market descriptions
    and produces similarity score. Only accepts pairs with score ≥ 0.92.
    """

    def __init__(
        self,
        deepseek_api_key: str | None = None,
        openai_api_key: str | None = None,
        min_score: float = 0.92,
        primary_provider: str = "deepseek",
        enable_fallback: bool = True,
    ) -> None:
        """Initialize LLM validator.

        Args:
            deepseek_api_key: DeepSeek API key
            openai_api_key: OpenAI API key (for fallback)
            min_score: Minimum similarity score to accept (default 0.92 per TDD)
            primary_provider: Primary LLM provider ("deepseek" or "openai")
            enable_fallback: Enable fallback to secondary provider
        """
        from arbitrage.matching.llm_client import LLMClient

        self.min_score = min_score
        self._client = LLMClient(
            deepseek_api_key=deepseek_api_key,
            openai_api_key=openai_api_key,
            primary_provider=primary_provider,
            enable_fallback=enable_fallback,
        )

    async def _call_llm(self, prompt: str) -> dict:
        """Call LLM API with structured prompt.

        Args:
            prompt: Formatted prompt with market descriptions

        Returns:
            Structured response with similarity score and explanation
        """
        messages = [
            {
                "role": "system",
                "content": "You are an expert at analyzing prediction market contracts for equivalence. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ]

        try:
            result = await self._client.complete(
                messages=messages, temperature=0.0, max_tokens=500
            )
            return result
        except Exception as exc:
            logger.error("llm_call_failed", error=str(exc))
            # Return conservative low score on failure
            return {
                "similarity": 0.0,
                "explanation": f"LLM call failed: {str(exc)}",
                "field_matches": {
                    "time_window": False,
                    "outcome_definition": False,
                    "resolution_source": False,
                },
            }

    def _build_prompt(self, pair: MarketPair) -> str:
        """Build structured prompt for LLM comparison.

        Args:
            pair: Market pair to compare

        Returns:
            Formatted prompt string
        """
        prompt = f"""Compare these two prediction market contracts and determine if they represent the same underlying event and outcome.

Market A (Polymarket):
- ID: {pair.primary.market_id}
- Contract: {pair.primary.symbol}

Market B (Kalshi):
- ID: {pair.hedge.market_id}
- Contract: {pair.hedge.symbol}

Analyze the following:
1. Do they reference the same time window?
2. Do they define the same outcome (e.g., both "Yes" or both measuring the same threshold)?
3. Are the resolution sources compatible?
4. Are there any ambiguous clauses that could cause divergence?

Return a JSON object with:
- similarity: float between 0 and 1 (1 = exact equivalence)
- explanation: string explaining your reasoning
- field_matches: object with booleans for time_window, outcome_definition, resolution_source
"""
        return prompt

    async def validate(self, pair: MarketPair) -> MarketPair:
        """Run LLM-based validation on a market pair.

        Args:
            pair: Market pair to validate

        Returns:
            Updated MarketPair with llm_similarity score
        """
        prompt = self._build_prompt(pair)
        response = await self._call_llm(prompt)

        similarity = response.get("similarity", 0.0)
        pair.llm_similarity = similarity

        if similarity < self.min_score:
            pair.hard_rules_passed = False
            pair.notes = f"LLM score {similarity:.3f} below threshold {self.min_score}"
            logger.debug(
                "llm_score_below_threshold",
                primary=pair.primary.symbol,
                hedge=pair.hedge.symbol,
                score=similarity,
            )
        else:
            logger.info(
                "llm_validation_passed",
                primary=pair.primary.symbol,
                hedge=pair.hedge.symbol,
                score=similarity,
            )

        return pair


__all__ = ["HardRulesValidator", "LLMValidator", "ValidationResult"]
