"""Preference-based scoring and ranking for recommendation candidates.

Scoring model (additive):
- Base score: candidate's own confidence value
- Positive behaviour signals boost items that share tokens with accepted/liked entities
- Negative behaviour signals penalise items sharing tokens with rejected entities
- Recently suggested items receive a diversity penalty (avoid repetition)
- Scores are clamped to [0.0, 1.0]

The result list is sorted by score descending.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.clock import as_utc
from app.models.signal import BehaviorSignal
from app.models.suggestion import Suggestion
from app.models.user import User

logger = logging.getLogger(__name__)

# Tuning knobs
_POSITIVE_SIGNAL_BOOST = 0.12
_NEGATIVE_SIGNAL_PENALTY = 0.15
_DIVERSITY_PENALTY = 0.20        # per recent duplicate
_RECENT_SUGGESTION_DAYS = 7      # window for diversity check
_RECENT_SIGNAL_DAYS = 30         # window for behaviour signal lookup


def _tokens(text: str) -> set[str]:
    """Extract meaningful word tokens from a string for fuzzy matching."""
    words = re.findall(r"\b[a-záéíóúüñ]{3,}\b", text.lower())
    # Remove common stop words
    stopwords = {
        "the", "and", "for", "with", "you", "your", "have", "that",
        "this", "are", "was", "not", "but", "has", "can", "try", "will",
        "been", "its", "from", "had", "eat", "use", "add", "get", "our",
    }
    return {w for w in words if w not in stopwords}


def _candidate_tokens(candidate: dict[str, Any]) -> set[str]:
    title = candidate.get("title", "")
    text = candidate.get("text", "")
    rationale = candidate.get("rationale", "")
    return _tokens(f"{title} {text} {rationale}")


def _signal_overlap(candidate_tokens: set[str], entity_name: str) -> float:
    """Return a fraction (0–1) of how much the entity_name overlaps with candidate tokens."""
    entity_tokens = _tokens(entity_name)
    if not entity_tokens or not candidate_tokens:
        return 0.0
    overlap = entity_tokens & candidate_tokens
    return len(overlap) / len(entity_tokens)


def score_candidates(
    candidates: list[dict[str, Any]],
    user: User,
    signals: list[BehaviorSignal],
    recent_suggestions: list[Suggestion],
) -> list[dict[str, Any]]:
    """Score and rank recommendation candidates.

    Args:
        candidates: List of suggestion dicts (from generators, post-filter).
        user: The User model instance.
        signals: Recent BehaviorSignal rows for the user.
        recent_suggestions: Recent Suggestion rows for context (diversity).

    Returns:
        The same list, each dict augmented with a "_score" key, sorted
        by _score descending.
    """
    if not candidates:
        return []

    cutoff_signals = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SIGNAL_DAYS)
    cutoff_suggestions = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SUGGESTION_DAYS)

    # Filter signals to recent window
    relevant_signals = [
        s for s in signals
        if s.created_at is None or as_utc(s.created_at) >= cutoff_signals
    ]

    # Positive and negative signal lists
    positive_signals = [
        s for s in relevant_signals
        if s.signal_type in (
            "accepted_suggestion",
            "repeated_meal_choice",
            "repeated_purchase",
            "repeated_activity",
        )
        or float(s.value) > 0
    ]
    negative_signals = [
        s for s in relevant_signals
        if s.signal_type in (
            "rejected_suggestion",
            "rejected_activity",
        )
        or float(s.value) < 0
    ]

    # Recent suggestion titles for diversity penalty
    recent_titles: set[str] = {
        s.title.lower()
        for s in recent_suggestions
        if s.created_at is None or as_utc(s.created_at) >= cutoff_suggestions
    }

    scored: list[dict[str, Any]] = []

    for candidate in candidates:
        base_score = float(candidate.get("confidence", 0.5))
        ctokens = _candidate_tokens(candidate)
        adjustment = 0.0

        # Positive signal boosts
        for sig in positive_signals:
            overlap = _signal_overlap(ctokens, sig.entity_name)
            if overlap > 0.3:
                boost = _POSITIVE_SIGNAL_BOOST * overlap * min(float(sig.value), 1.0)
                adjustment += boost
                logger.debug(
                    "Candidate %r: +%.3f from positive signal %r (overlap=%.2f)",
                    candidate.get("title"), boost, sig.entity_name, overlap,
                )

        # Negative signal penalties
        for sig in negative_signals:
            overlap = _signal_overlap(ctokens, sig.entity_name)
            if overlap > 0.3:
                penalty = _NEGATIVE_SIGNAL_PENALTY * overlap * min(abs(float(sig.value)), 1.0)
                adjustment -= penalty
                logger.debug(
                    "Candidate %r: -%.3f from negative signal %r (overlap=%.2f)",
                    candidate.get("title"), penalty, sig.entity_name, overlap,
                )

        # Diversity penalty for recently shown suggestions
        title_lower = candidate.get("title", "").lower()
        if title_lower in recent_titles:
            adjustment -= _DIVERSITY_PENALTY
            logger.debug("Candidate %r: -%.2f diversity penalty.", candidate.get("title"), _DIVERSITY_PENALTY)
        else:
            # Check partial overlap with recent suggestion titles
            for rt in recent_titles:
                rt_tokens = _tokens(rt)
                if rt_tokens and ctokens:
                    overlap = len(rt_tokens & ctokens) / len(rt_tokens)
                    if overlap >= 0.6:
                        adjustment -= _DIVERSITY_PENALTY * overlap
                        break

        final_score = max(0.0, min(1.0, base_score + adjustment))
        scored_candidate = {**candidate, "_score": round(final_score, 4)}
        scored.append(scored_candidate)

    scored.sort(key=lambda c: c["_score"], reverse=True)
    return scored
