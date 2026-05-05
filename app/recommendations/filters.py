"""Hard constraint filtering for recommendations.

Removes candidates that violate explicit user constraints:
- Impossible activities (e.g. user can't swim due to injury)
- Disliked/avoided foods
- Strong negative RecommendationPreference entries

These filters are applied BEFORE scoring — candidates that fail are
completely removed, not just penalised.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.suggestion import RecommendationPreference
from app.models.user import User

logger = logging.getLogger(__name__)


def _normalise(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _any_token_matches(candidate_text: str, blocked_terms: set[str]) -> bool:
    """Return True if any blocked term appears as a substring in the candidate text."""
    norm = _normalise(candidate_text)
    for term in blocked_terms:
        if term and term in norm:
            return True
    return False


def _build_blocked_set(
    user: User,
    preferences: list[RecommendationPreference],
    constraint_type: str,
) -> set[str]:
    """Build a set of normalised blocked terms for a given constraint type.

    constraint_type: "food" | "activity"
    """
    blocked: set[str] = set()

    if constraint_type == "food":
        for lst in (
            user.disliked_foods_json or [],
            user.dietary_restrictions_json or [],
        ):
            for item in lst:
                blocked.add(_normalise(item))

        for pref in preferences:
            if pref.item_type in ("food", "ingredient", "recipe", "cuisine") and pref.preference_signal in (
                "dislikes",
                "impossible",
                "avoid",
            ):
                blocked.add(_normalise(pref.item_name))

    elif constraint_type == "activity":
        for lst in (
            user.impossible_activities_json or [],
            user.disliked_activities_json or [],
        ):
            for item in lst:
                blocked.add(_normalise(item))

        for pref in preferences:
            if pref.item_type == "exercise" and pref.preference_signal in (
                "impossible",
                "dislikes",
                "avoid",
            ):
                blocked.add(_normalise(pref.item_name))

    return blocked


def _infer_category(candidate: dict[str, Any]) -> str | None:
    """Infer whether a candidate is food- or activity-related from its category field."""
    cat = candidate.get("category", "").lower()
    if cat in ("meal", "shopping", "pantry"):
        return "food"
    if cat in ("activity", "workout", "exercise", "recovery"):
        return "activity"
    # Fall back to scanning text
    text = (candidate.get("title", "") + " " + candidate.get("text", "")).lower()
    if any(w in text for w in ("eat", "food", "meal", "recipe", "cook", "drink", "pantry")):
        return "food"
    if any(w in text for w in ("workout", "gym", "run", "swim", "bike", "yoga", "exercise")):
        return "activity"
    return None


def apply_signal_constraints(
    candidates: list[dict[str, Any]],
    signals: list[Any],
    rejection_overlap_threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """Remove candidates that strongly overlap with recently-rejected BehaviorSignals.

    This is a hard pre-filter (complements scorer penalties): if the user has
    clearly rejected something (value < 0, high token overlap), don't show it
    again regardless of how scoring would rank it.

    Args:
        candidates: Filtered candidate dicts from apply_hard_constraints.
        signals: Recent BehaviorSignal rows (pre-filtered to relevant window).
        rejection_overlap_threshold: Token overlap fraction above which a
            candidate is hard-excluded. Default 0.6 (60%).

    Returns:
        Candidates with strongly-rejected items removed.
    """
    rejected_entities = [
        s.entity_name
        for s in signals
        if float(s.value) < 0 and s.signal_type in ("rejected_suggestion", "rejected_activity")
    ]
    if not rejected_entities:
        return candidates

    kept: list[dict[str, Any]] = []
    removed = 0
    for candidate in candidates:
        ctokens = set(re.findall(r"\b[a-záéíóúüñ]{3,}\b", _normalise(
            candidate.get("title", "") + " " + candidate.get("text", "")
        )))
        blocked = False
        for entity_name in rejected_entities:
            entity_tokens = set(re.findall(r"\b[a-záéíóúüñ]{3,}\b", _normalise(entity_name)))
            if entity_tokens and ctokens:
                overlap = len(entity_tokens & ctokens) / len(entity_tokens)
                if overlap >= rejection_overlap_threshold:
                    blocked = True
                    break
        if blocked:
            logger.debug("Signal-constrained out candidate: %r", candidate.get("title"))
            removed += 1
        else:
            kept.append(candidate)

    if removed:
        logger.info("Signal constraint filter removed %d candidate(s).", removed)
    return kept


def apply_hard_constraints(
    candidates: list[dict[str, Any]],
    user: User,
    preferences: list[RecommendationPreference],
) -> list[dict[str, Any]]:
    """Remove candidates that violate hard constraints.

    Args:
        candidates: Raw list of suggestion dicts from generators.
        user: The User model instance for this recommendation context.
        preferences: All RecommendationPreference rows for the user.

    Returns:
        Filtered list with constraint-violating candidates removed.
    """
    blocked_food = _build_blocked_set(user, preferences, "food")
    blocked_activity = _build_blocked_set(user, preferences, "activity")

    if not blocked_food and not blocked_activity:
        return candidates  # fast path

    kept: list[dict[str, Any]] = []
    removed = 0

    for candidate in candidates:
        cat = _infer_category(candidate)
        full_text = _normalise(
            candidate.get("title", "") + " " + candidate.get("text", "")
        )

        if cat == "food" and blocked_food:
            if _any_token_matches(full_text, blocked_food):
                logger.debug("Filtered out food suggestion: %r", candidate.get("title"))
                removed += 1
                continue

        if cat == "activity" and blocked_activity:
            if _any_token_matches(full_text, blocked_activity):
                logger.debug("Filtered out activity suggestion: %r", candidate.get("title"))
                removed += 1
                continue

        kept.append(candidate)

    if removed:
        logger.info("Hard constraint filter removed %d candidate(s).", removed)

    return kept
