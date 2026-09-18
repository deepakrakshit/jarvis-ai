"""JARVIS Token Estimation Subsystem.

Provides deterministic, model-aware token counting and estimation for dynamic
budgeting and artifact offloading.
"""

import math
from abc import ABC, abstractmethod
from typing import Any


class TokenEstimator(ABC):
    """Abstract interface for token estimation."""

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for raw text."""
        ...

    def estimate_message_tokens(self, message: dict[str, Any]) -> int:
        """Estimate token count for a message dictionary including structure overhead."""
        tokens = 4  # Baseline overhead per message (role tag, delimiters)
        for _key, val in message.items():
            if isinstance(val, str):
                tokens += self.estimate_tokens(val)
            elif isinstance(val, (int, float, bool, dict)):
                tokens += self.estimate_tokens(str(val))
            elif isinstance(val, list):
                for item in val:
                    tokens += self.estimate_tokens(str(item))
        return tokens


class HeuristicTokenEstimator(TokenEstimator):
    """Deterministic, zero-dependency token estimator using language/code heuristics."""

    def __init__(self, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        self.chars_per_token = chars_per_token

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens using character-length heuristic and whitespace adjustments."""
        if not text:
            return 0

        char_len = len(text)
        # Account for density in code/dense tokens (punctuation, special chars)
        special_char_count = sum(1 for c in text if not (c.isalnum() or c.isspace()))
        effective_len = char_len + (special_char_count * 0.5)

        estimated = math.ceil(effective_len / self.chars_per_token)
        return max(1, estimated)


class TiktokenEstimator(TokenEstimator):
    """Accurate token estimator leveraging tiktoken when available, with fallback."""

    def __init__(
        self,
        encoding_name: str = "cl100k_base",
        fallback: TokenEstimator | None = None,
    ) -> None:
        self.fallback = fallback or HeuristicTokenEstimator()
        self._encoding: Any = None
        self._available = False

        try:
            import tiktoken  # type: ignore[import-not-found]

            self._encoding = tiktoken.get_encoding(encoding_name)
            self._available = True
        except (ImportError, Exception):
            self._available = False

    @property
    def is_available(self) -> bool:
        """Return True if tiktoken library and encoding are operational."""
        return self._available

    def estimate_tokens(self, text: str) -> int:
        """Count tokens using tiktoken if loaded, otherwise fallback."""
        if not text:
            return 0
        if self._available and self._encoding is not None:
            try:
                return len(self._encoding.encode(text, disallowed_special=()))
            except Exception:
                return self.fallback.estimate_tokens(text)
        return self.fallback.estimate_tokens(text)


def create_token_estimator(
    model_id: str | None = None,
    chars_per_token: float = 4.0,
) -> TokenEstimator:
    """Factory helper creating the best available estimator for the specified model."""
    tiktoken_est = TiktokenEstimator()
    if tiktoken_est.is_available:
        return tiktoken_est
    return HeuristicTokenEstimator(chars_per_token=chars_per_token)
