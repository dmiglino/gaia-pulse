"""Pydantic models for parsed NLP intents.

Layer-agnostic data structures used by both the rule-based parser (Layer 1)
and the LLM adapter (Layer 2).  All models use Python 3.12 type hints.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

UserKey = Literal["diego", "rocio", "both"]

PreferenceSignal = Literal["likes", "dislikes", "impossible", "avoid", "preferred"]

IntentType = Literal[
    "log_meal",
    "log_workout",
    "log_body_metric",
    "add_stock",
    "consume_stock",
    "update_preference",
    "mixed",
]


class FoodItemRef(BaseModel):
    """A parsed reference to a food item with optional quantity."""

    food_name: str
    qty: float | None = None
    unit: str | None = None  # g/kg/ml/l/unit/piece/serving/…


class StockItemRef(BaseModel):
    """Item used in stock add / consume intents."""

    food_name: str
    quantity: float | None = None
    unit: str | None = None


class ExerciseRef(BaseModel):
    """A parsed exercise within a workout."""

    name: str
    muscle_group: str | None = None
    duration_minutes: int | None = None
    sets: int | None = None
    reps: int | None = None


# ---------------------------------------------------------------------------
# Base intent
# ---------------------------------------------------------------------------


class ParsedIntent(BaseModel):
    """Common fields present on every parsed intent."""

    intent_type: IntentType
    participants: list[UserKey] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    raw_span: str | None = None  # The text fragment that produced this intent
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Specific intent subtypes
# ---------------------------------------------------------------------------


class MealIntent(ParsedIntent):
    """A meal logging intent."""

    intent_type: IntentType = "log_meal"
    meal_type: str = "other"  # breakfast/lunch/dinner/snack/other
    context: str = "home"  # home/restaurant/outside/travel/other
    time_reference: str | None = None  # today/yesterday/tonight/this morning/…
    # Per-user food items.  Key is "diego" | "rocio" | "both".
    items_per_user: dict[str, list[FoodItemRef]] = Field(default_factory=dict)


class WorkoutIntent(ParsedIntent):
    """A workout logging intent."""

    intent_type: IntentType = "log_workout"
    workout_type: str | None = None  # gym/outdoor/home/yoga/sports/…
    duration_minutes: int | None = None
    location: str | None = None
    exercises: list[ExerciseRef] = Field(default_factory=list)


class BodyMetricIntent(ParsedIntent):
    """A body metric logging intent (always for a single user)."""

    intent_type: IntentType = "log_body_metric"
    user_key: UserKey = "diego"
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    waist_cm: float | None = None
    sleep_hours: float | None = None


class StockAddIntent(ParsedIntent):
    """Pantry addition intent ('we bought …')."""

    intent_type: IntentType = "add_stock"
    items: list[StockItemRef] = Field(default_factory=list)


class StockConsumeIntent(ParsedIntent):
    """Pantry consumption intent ('we used …')."""

    intent_type: IntentType = "consume_stock"
    items: list[StockItemRef] = Field(default_factory=list)


class PreferenceIntent(ParsedIntent):
    """A preference update intent ('don't suggest X', 'we like Y')."""

    intent_type: IntentType = "update_preference"
    user_key: UserKey = "both"
    item_type: str = "exercise"  # food/exercise/recipe/meal_type/cuisine/…
    item_name: str = ""
    preference_signal: PreferenceSignal = "dislikes"


# ---------------------------------------------------------------------------
# Top-level parse result
# ---------------------------------------------------------------------------


class ParseResult(BaseModel):
    """Output of any NLP parsing layer."""

    intents: list[
        MealIntent
        | WorkoutIntent
        | BodyMetricIntent
        | StockAddIntent
        | StockConsumeIntent
        | PreferenceIntent
        | ParsedIntent
    ] = Field(default_factory=list)
    overall_confidence: float = Field(0.0, ge=0.0, le=1.0)
    parser_layer: Literal["rules", "llm", "combined"] = "rules"
    raw_text: str = ""
