"""Candidate generation using lexical and entity-based blocking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from structlog import get_logger

from arbitrage.domain.markets import Market, Venue
from arbitrage.markets.pairs import MarketPair, MarketWindow

logger = get_logger(__name__)


@dataclass
class BlockingKey:
    """Composite key for blocking candidate pairs."""

    category: str | None
    entities: frozenset[str]
    date_tokens: frozenset[str]
    numeric_thresholds: frozenset[str]

    def __hash__(self) -> int:
        return hash((self.category, self.entities, self.date_tokens, self.numeric_thresholds))


class CandidateGenerator:
    """Generates candidate market pairs using lexical and entity blocking.

    Implements TDD section 5.3: Uses normalized n-grams, symbols, dates, and
    Jaccard similarity on entities to reduce the candidate space before LLM ranking.
    """

    def __init__(
        self,
        polymarket_markets: list[Market],
        kalshi_markets: list[Market],
        min_jaccard: float = 0.3,
    ) -> None:
        """Initialize candidate generator.

        Args:
            polymarket_markets: Markets from Polymarket venue
            kalshi_markets: Markets from Kalshi venue
            min_jaccard: Minimum Jaccard similarity for entity overlap (default 0.3)
        """
        self.polymarket_markets = polymarket_markets
        self.kalshi_markets = kalshi_markets
        self.min_jaccard = min_jaccard

    @staticmethod
    def _extract_entities(text: str) -> set[str]:
        """Extract normalized entities from market text.

        Extracts:
        - Uppercase words (likely entities/tickers)
        - Numbers with units (e.g., "3.0%", "$100")
        - Date-like patterns

        Args:
            text: Market title or contract name

        Returns:
            Set of normalized entity tokens
        """
        entities = set()

        # Extract uppercase words (at least 2 chars)
        uppercase = re.findall(r"\b[A-Z]{2,}\b", text)
        entities.update(tok.lower() for tok in uppercase)

        # Extract numbers with units or currency symbols
        numbers_with_units = re.findall(r"[\$€£¥]?\d+\.?\d*%?", text)
        entities.update(numbers_with_units)

        # Extract words that are likely entity names (capitalized)
        capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        for cap in capitalized:
            if len(cap) > 3:  # Filter short words
                entities.add(cap.lower())

        return entities

    @staticmethod
    def _extract_dates(text: str) -> set[str]:
        """Extract date tokens from text.

        Args:
            text: Market title or contract name

        Returns:
            Set of normalized date tokens (months, years)
        """
        dates = set()

        # Month names
        months = re.findall(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
            text.lower(),
        )
        dates.update(months)

        # Years (2023, 2024, etc)
        years = re.findall(r"\b20\d{2}\b", text)
        dates.update(years)

        # Quarters (Q1, Q2, etc)
        quarters = re.findall(r"\bq[1-4]\b", text.lower())
        dates.update(quarters)

        return dates

    @staticmethod
    def _extract_numeric_thresholds(text: str) -> set[str]:
        """Extract numeric thresholds and comparison operators.

        Args:
            text: Market title or contract name

        Returns:
            Set of threshold expressions (e.g., ">3.0", "≥100")
        """
        thresholds = set()

        # Look for patterns like "above 3.0", "over $100", "≥ 50"
        patterns = [
            r"(?:above|over|exceed[s]?|≥|>=)\s*[\$€£¥]?\d+\.?\d*%?",
            r"(?:below|under|less than|≤|<=)\s*[\$€£¥]?\d+\.?\d*%?",
            r"[\$€£¥]?\d+\.?\d*%?\s*(?:or more|or less|and above|and below)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text.lower())
            thresholds.update(matches)

        return thresholds

    def _create_blocking_key(self, market: Market) -> BlockingKey:
        """Create a blocking key for a market.

        Args:
            market: Market to create key for

        Returns:
            BlockingKey for indexing
        """
        # Combine contract name and event name for richer text
        full_text = f"{market.event_name} {market.contract_name}"

        entities = self._extract_entities(full_text)
        dates = self._extract_dates(full_text)
        thresholds = self._extract_numeric_thresholds(full_text)

        # Normalize category (use tags if available)
        category = None
        if market.tags:
            category = market.tags[0].lower() if market.tags else None

        return BlockingKey(
            category=category,
            entities=frozenset(entities),
            date_tokens=frozenset(dates),
            numeric_thresholds=frozenset(thresholds),
        )

    @staticmethod
    def _jaccard_similarity(set_a: frozenset, set_b: frozenset) -> float:
        """Compute Jaccard similarity between two sets.

        Args:
            set_a: First set
            set_b: Second set

        Returns:
            Jaccard similarity coefficient in [0, 1]
        """
        if not set_a and not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _is_candidate_pair(self, key_a: BlockingKey, key_b: BlockingKey) -> bool:
        """Determine if two blocking keys represent a candidate pair.

        Args:
            key_a: Blocking key from venue A
            key_b: Blocking key from venue B

        Returns:
            True if keys meet blocking criteria
        """
        # Category must match if both are specified
        if key_a.category and key_b.category and key_a.category != key_b.category:
            return False

        # Date tokens should overlap significantly
        date_jaccard = self._jaccard_similarity(key_a.date_tokens, key_b.date_tokens)
        if date_jaccard < 0.5 and (key_a.date_tokens or key_b.date_tokens):
            return False

        # Entities should have some overlap
        entity_jaccard = self._jaccard_similarity(key_a.entities, key_b.entities)
        if entity_jaccard < self.min_jaccard:
            return False

        return True

    def generate(self) -> Iterable[MarketPair]:
        """Generate candidate pairs using blocking strategies.

        Yields:
            MarketPair candidates that pass blocking filters
        """
        # Create blocking indices
        poly_keys = {m.id: self._create_blocking_key(m) for m in self.polymarket_markets}
        kalshi_keys = {m.id: self._create_blocking_key(m) for m in self.kalshi_markets}

        candidate_count = 0
        blocked_count = 0

        for poly_market in self.polymarket_markets:
            poly_key = poly_keys[poly_market.id]

            for kalshi_market in self.kalshi_markets:
                kalshi_key = kalshi_keys[kalshi_market.id]

                # Apply blocking criteria
                if not self._is_candidate_pair(poly_key, kalshi_key):
                    blocked_count += 1
                    continue

                # Create MarketPair with initial placeholder values
                # LLM similarity and validation will be filled by validators
                from arbitrage.events.models import MarketReference

                pair = MarketPair(
                    primary=MarketReference(
                        venue=poly_market.venue.value,
                        market_id=poly_market.venue_market_id,
                        symbol=poly_market.contract_name,
                    ),
                    hedge=MarketReference(
                        venue=kalshi_market.venue.value,
                        market_id=kalshi_market.venue_market_id,
                        symbol=kalshi_market.contract_name,
                    ),
                    window=MarketWindow(
                        open_time=min(poly_market.open_time, kalshi_market.open_time),
                        close_time=max(poly_market.close_time, kalshi_market.close_time),
                        resolution_time=max(poly_market.close_time, kalshi_market.close_time),
                    ),
                    llm_similarity=0.0,  # Placeholder, will be set by LLM validator
                    hard_rules_passed=False,  # Will be validated by rule checker
                    last_validated=datetime.utcnow(),
                )

                candidate_count += 1
                yield pair

        logger.info(
            "candidate_generation_complete",
            candidates=candidate_count,
            blocked=blocked_count,
            reduction_pct=round(100 * blocked_count / (candidate_count + blocked_count), 1)
            if (candidate_count + blocked_count) > 0
            else 0,
        )


__all__ = ["CandidateGenerator", "BlockingKey"]
