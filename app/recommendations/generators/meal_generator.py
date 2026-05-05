"""Meal suggestion generator.

Generates meal suggestions for a user based on:
- Current pantry stock (prefer items that are available / nearly expiring)
- Recent meal history (avoid recent repetition)
- User dietary preferences and restrictions
- Time-of-day context (meal type)
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.models.pantry import PantryStock
from app.models.suggestion import RecommendationPreference
from app.models.user import User

logger = logging.getLogger(__name__)

_RECENCY_DAYS = 7          # look-back window for repetition check
_MAX_SUGGESTIONS = 8
_LOW_STOCK_PCT = 0.25      # stock at or below 25% of threshold = low


def _current_meal_type() -> str:
    """Infer meal type from current local hour (UTC approximation)."""
    hour = datetime.now(tz=timezone.utc).hour
    if 5 <= hour < 10:
        return "breakfast"
    if 10 <= hour < 14:
        return "lunch"
    if 14 <= hour < 18:
        return "snack"
    return "dinner"


def _get_recent_food_names(db: Session, user: User, days: int = _RECENCY_DAYS) -> Counter[str]:
    """Return a Counter of food names consumed by the user in the last N days."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    rows = (
        db.query(MealItemConsumed.normalized_free_text_name)
        .join(MealParticipant, MealParticipant.id == MealItemConsumed.meal_participant_id)
        .join(MealEvent, MealEvent.id == MealParticipant.meal_event_id)
        .filter(
            MealParticipant.user_id == user.id,
            MealEvent.timestamp >= cutoff,
        )
        .all()
    )
    return Counter(r[0].lower() for r in rows)


def _get_pantry_items(db: Session, household_id: int) -> list[PantryStock]:
    return (
        db.query(PantryStock)
        .filter(PantryStock.household_id == household_id)
        .filter(PantryStock.current_quantity > 0)
        .all()
    )


def _get_disliked_names(user: User, preferences: list[RecommendationPreference]) -> set[str]:
    disliked: set[str] = set()
    # From User model fields
    for lst in (user.disliked_foods_json or [], user.dietary_restrictions_json or []):
        for item in lst:
            disliked.add(item.lower())
    # From explicit preferences
    for pref in preferences:
        if pref.item_type in ("food", "ingredient", "recipe") and pref.preference_signal in (
            "dislikes",
            "impossible",
            "avoid",
        ):
            disliked.add(pref.item_name.lower())
    return disliked


def generate(
    db: Session,
    user: User,
    preferences: list[RecommendationPreference],
    meal_type: str | None = None,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate meal suggestions for *user*.

    Returns a list of suggestion dicts suitable for building Suggestion records.
    Each dict has: title, text, rationale, evidence_summary, confidence, source_type, category.
    """
    if meal_type is None:
        meal_type = _current_meal_type()

    recent_foods = _get_recent_food_names(db, user)
    disliked = _get_disliked_names(user, preferences)
    pantry = _get_pantry_items(db, user.household_id)

    suggestions: list[dict[str, Any]] = []

    # ── 1. Use-what-you-have: suggest meals from pantry items ─────────────
    pantry_names = []
    for stock in pantry:
        food: FoodItem | None = stock.food_item
        if food is None:
            continue
        name = food.canonical_name.lower()
        if name in disliked:
            continue
        pantry_names.append((name, float(stock.current_quantity), stock.unit))

    if pantry_names:
        # Sort by quantity ascending — use items before they run out
        pantry_names.sort(key=lambda x: x[1])
        featured = [n for n, _, _ in pantry_names[:5]]
        if featured:
            title = f"Use your {featured[0]} today"
            text = (
                f"You have {', '.join(featured)} in your pantry. "
                f"Consider incorporating them into {meal_type}."
            )
            freq_penalty = sum(recent_foods.get(f, 0) for f in featured)
            confidence = max(0.5, 0.85 - 0.05 * freq_penalty)
            suggestions.append(
                {
                    "category": "meal",
                    "title": title,
                    "text": text,
                    "rationale": "Items available in pantry that should be used.",
                    "evidence_summary": (
                        f"Pantry items: {', '.join(featured)}. "
                        f"Recent frequency score: {freq_penalty}."
                    ),
                    "confidence": round(confidence, 3),
                    "source_type": "stock",
                }
            )

    # ── 2. Variety: suggest foods not eaten recently ──────────────────────
    all_recent = set(recent_foods.keys())
    variety_candidates = [n for n, _, _ in pantry_names if n not in all_recent]
    if variety_candidates:
        pick = variety_candidates[0]
        suggestions.append(
            {
                "category": "meal",
                "title": f"Try {pick.title()} for variety",
                "text": (
                    f"You haven't had {pick} recently. "
                    f"It's available in your pantry — a good option for {meal_type}."
                ),
                "rationale": "Dietary variety supports micronutrient balance.",
                "evidence_summary": f"{pick} not consumed in past {_RECENCY_DAYS} days.",
                "confidence": 0.7,
                "source_type": "rule",
            }
        )

    # ── 3. Preferred foods from explicit preferences ───────────────────────
    liked_prefs = [
        p for p in preferences
        if p.item_type in ("food", "ingredient", "recipe")
        and p.preference_signal in ("likes", "preferred")
    ]
    for pref in liked_prefs[:3]:
        name = pref.item_name.lower()
        if name in disliked:
            continue
        recent_count = recent_foods.get(name, 0)
        if recent_count >= 3:
            # Already eating it a lot — skip
            continue
        suggestions.append(
            {
                "category": "meal",
                "title": f"Have {pref.item_name} today",
                "text": (
                    f"Based on your preferences, {pref.item_name} is a great option "
                    f"for {meal_type}."
                ),
                "rationale": "Matches explicit food preference.",
                "evidence_summary": (
                    f"User preference signal: {pref.preference_signal}. "
                    f"Recent occurrences: {recent_count}."
                ),
                "confidence": min(0.6 + float(pref.strength) * 0.3, 0.95),
                "source_type": "preference",
            }
        )

    # ── 4. Low-stock alert: use near-empty items ───────────────────────────
    for stock in pantry:
        if not stock.is_low:
            continue
        food = stock.food_item
        if food is None:
            continue
        name = food.canonical_name.lower()
        if name in disliked:
            continue
        suggestions.append(
            {
                "category": "meal",
                "title": f"Use your last {food.canonical_name}",
                "text": (
                    f"Your {food.canonical_name} stock is running low "
                    f"({stock.current_quantity} {stock.unit} remaining). "
                    "Consider using it before it goes bad."
                ),
                "rationale": "Item is near depletion — use to avoid waste.",
                "evidence_summary": (
                    f"Current stock: {stock.current_quantity} {stock.unit}. "
                    f"Threshold: {stock.low_stock_threshold}."
                ),
                "confidence": 0.8,
                "source_type": "stock",
            }
        )

    # De-duplicate by title and respect limit
    seen_titles: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["title"] not in seen_titles:
            seen_titles.add(s["title"])
            unique.append(s)

    return unique[:limit]
