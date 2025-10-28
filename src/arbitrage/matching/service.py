"""Market matching pipeline for identifying equivalent binary markets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from arbitrage.markets.pairs import MarketPair


class CandidateGenerator(Protocol):
    """Generates candidate market pairs for validation."""

    def generate(self) -> Iterable[MarketPair]:
        ...


class Validator(Protocol):
    """Validates whether a candidate pair represents the same underlying event."""

    def validate(self, pair: MarketPair) -> MarketPair:
        ...


@dataclass(slots=True)
class MatchingService:
    """Coordinates candidate generation and validation for market pairs."""

    generator: CandidateGenerator
    validators: list[Validator]

    def run(self) -> Iterable[MarketPair]:
        """Yield validated market pairs ready for signal computation."""

        for candidate in self.generator.generate():
            validated = candidate
            for validator in self.validators:
                validated = validator.validate(validated)
                if not validated.hard_rules_passed:
                    break
            if validated.hard_rules_passed:
                yield validated


__all__ = ["CandidateGenerator", "MatchingService", "Validator"]
