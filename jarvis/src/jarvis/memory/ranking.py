"""Memory Ranking Algorithms: Temporal Decay and Maximal Marginal Relevance (MMR).

Adapts the proven MMR and temporal decay ranking strategies from the core
substrate to ensure diversity, novelty, and time-aware relevance in memory recall.
"""

import math
import re
from dataclasses import dataclass
from typing import Callable, Generic, List, Optional, Set, TypeVar

T = TypeVar("T")

WORD_SPLIT_REGEX = re.compile(r"[\s\-_.,;!?()[\]{}<>/\\:\"']+")


def tokenize(text: str) -> Set[str]:
    """Tokenize arbitrary text into a set of lowercased alphanumeric terms."""
    if not text:
        return set()
    return {part.lower() for part in WORD_SPLIT_REGEX.split(text) if len(part) > 1}


def jaccard_similarity(tokens_a: Set[str], tokens_b: Set[str]) -> float:
    """Compute Jaccard similarity coefficient between two sets of tokens."""
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection_len = len(tokens_a.intersection(tokens_b))
    union_len = len(tokens_a.union(tokens_b))
    if union_len == 0:
        return 0.0
    return intersection_len / union_len


def apply_temporal_decay(
    score: float,
    age_days: float,
    half_life_days: float = 30.0,
    is_evergreen: bool = False,
) -> float:
    """Apply half-life exponential decay to a relevance score.

    Score decays according to: score * exp(-(ln(2) / half_life_days) * age_days).
    Evergreen knowledge (e.g. core identity, persistent user preferences) does not decay.
    """
    if is_evergreen:
        return score
    if half_life_days <= 0 or not math.isfinite(half_life_days):
        return score
    clamped_age = max(0.0, age_days)
    decay_factor = math.exp(-(math.log(2.0) / half_life_days) * clamped_age)
    return score * decay_factor


@dataclass
class _PreparedCandidate(Generic[T]):
    item: T
    raw_score: float
    relevance: float
    tokens: Set[str]
    max_similarity: float = 0.0


def mmr_rerank(
    items: List[T],
    score_fn: Callable[[T], float],
    snippet_fn: Callable[[T], str],
    lambda_param: float = 0.7,
    limit: Optional[int] = None,
) -> List[T]:
    """Re-rank candidate items using Maximal Marginal Relevance (MMR).

    MMR balances relevance and novelty by iteratively selecting items that maximize:
        MMR = lambda * relevance - (1 - lambda) * max_similarity_to_already_selected

    Args:
        items: Candidate items to re-rank.
        score_fn: Function extracting raw relevance score from an item.
        snippet_fn: Function extracting representative text snippet for similarity.
        lambda_param: Weighting parameter between 0 (maximum diversity) and 1 (pure relevance).
        limit: Optional maximum number of items to return.

    Returns:
        Re-ranked items in optimal order.
    """
    if len(items) <= 1:
        return list(items)

    clamped_lambda = max(0.0, min(1.0, lambda_param))
    target_limit = limit or len(items)

    # If lambda is 1.0, return items strictly sorted by score
    if clamped_lambda == 1.0:
        sorted_by_score = sorted(items, key=score_fn, reverse=True)
        return sorted_by_score[:target_limit]

    # Precompute tokens and normalize scores
    scores = [score_fn(it) for it in items]
    min_score = min(scores)
    max_score = max(scores)
    score_range = max_score - min_score

    prepared: List[_PreparedCandidate[T]] = []
    for it, raw_score in zip(items, scores, strict=True):
        normalized_relevance = 1.0 if score_range == 0 else (raw_score - min_score) / score_range
        snippet = snippet_fn(it)
        tokens = tokenize(snippet)
        prepared.append(
            _PreparedCandidate(
                item=it,
                raw_score=raw_score,
                relevance=normalized_relevance,
                tokens=tokens,
                max_similarity=0.0,
            )
        )

    remaining = list(prepared)
    selected: List[T] = []

    while remaining and len(selected) < target_limit:
        best_candidate: Optional[_PreparedCandidate[T]] = None
        best_mmr_score = -float("inf")

        for candidate in remaining:
            mmr_score = (
                clamped_lambda * candidate.relevance
                - (1.0 - clamped_lambda) * candidate.max_similarity
            )
            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_candidate = candidate
            elif mmr_score == best_mmr_score and best_candidate is not None:
                if candidate.raw_score > best_candidate.raw_score:
                    best_candidate = candidate

        if best_candidate is None:
            break

        selected.append(best_candidate.item)
        remaining.remove(best_candidate)

        # Update max similarity to any selected item for all remaining candidates
        best_tokens = best_candidate.tokens
        for candidate in remaining:
            sim = jaccard_similarity(candidate.tokens, best_tokens)
            if sim > candidate.max_similarity:
                candidate.max_similarity = sim

    return selected
