"""Market matching pipeline for identifying equivalent binary markets."""

from arbitrage.matching.candidate import BlockingKey, CandidateGenerator
from arbitrage.matching.service import CandidateGenerator as CandidateGeneratorProtocol
from arbitrage.matching.service import MatchingService, Validator
from arbitrage.matching.validators import HardRulesValidator, LLMValidator, ValidationResult

__all__ = [
    "BlockingKey",
    "CandidateGenerator",
    "CandidateGeneratorProtocol",
    "HardRulesValidator",
    "LLMValidator",
    "MatchingService",
    "ValidationResult",
    "Validator",
]
