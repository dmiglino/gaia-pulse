"""OpenAI-compatible LLM adapter for Layer 2 NLP parsing.

Uses the OpenAI function-calling / structured-output API to improve upon the
rule-based Layer 1 result.  Falls back gracefully to the Layer 1 result on
any error (network failure, quota, bad response, etc.).

The adapter is async and uses the official ``openai`` AsyncClient.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.core.config import get_settings
from app.nlp.adapters.base import BaseLLMAdapter
from app.nlp.intents import (
    BodyMetricIntent,
    ExerciseRef,
    FoodItemRef,
    MealIntent,
    ParseResult,
    ParsedIntent,
    PreferenceIntent,
    StockAddIntent,
    StockConsumeIntent,
    StockItemRef,
    UserKey,
    WorkoutIntent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON schema for the function the LLM must call
# ---------------------------------------------------------------------------

_PARSE_FUNCTION_SCHEMA: dict[str, Any] = {
    "name": "parse_wellness_input",
    "description": (
        "Parse a natural-language wellness log entry for the GaiaPulse household app. "
        "Return a structured list of intents extracted from the text."
    ),
    "parameters": {
        "type": "object",
        "required": ["intents", "overall_confidence"],
        "properties": {
            "overall_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How confident the parser is that all intents were correctly extracted.",
            },
            "intents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["intent_type", "participants", "confidence"],
                    "properties": {
                        "intent_type": {
                            "type": "string",
                            "enum": [
                                "log_meal",
                                "log_workout",
                                "log_body_metric",
                                "add_stock",
                                "consume_stock",
                                "update_preference",
                                "mixed",
                            ],
                        },
                        "participants": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["diego", "rocio", "both"]},
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        # meal fields
                        "meal_type": {"type": "string"},
                        "context": {"type": "string"},
                        "time_reference": {"type": "string"},
                        "items_per_user": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        # `food_name`, not `name`: that is the field
                                        # `FoodItemRef` declares, and the stock schema
                                        # below already uses the same word.
                                        "food_name": {"type": "string"},
                                        "qty": {"type": "number"},
                                        "unit": {"type": "string"},
                                    },
                                    "required": ["food_name"],
                                },
                            },
                        },
                        # workout fields
                        "workout_type": {"type": "string"},
                        "duration_minutes": {"type": "integer"},
                        "location": {"type": "string"},
                        "exercises": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "muscle_group": {"type": "string"},
                                    "duration_minutes": {"type": "integer"},
                                    "sets": {"type": "integer"},
                                    "reps": {"type": "integer"},
                                },
                                "required": ["name"],
                            },
                        },
                        # body metric fields
                        "user_key": {"type": "string", "enum": ["diego", "rocio", "both"]},
                        "weight_kg": {"type": "number"},
                        "body_fat_pct": {"type": "number"},
                        "waist_cm": {"type": "number"},
                        "sleep_hours": {"type": "number"},
                        # stock fields
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "food_name": {"type": "string"},
                                    "quantity": {"type": "number"},
                                    "unit": {"type": "string"},
                                },
                                "required": ["food_name"],
                            },
                        },
                        # preference fields
                        "item_type": {"type": "string"},
                        "item_name": {"type": "string"},
                        "preference_signal": {
                            "type": "string",
                            "enum": ["likes", "dislikes", "impossible", "avoid", "preferred"],
                        },
                    },
                },
            },
        },
    },
}

_SYSTEM_PROMPT = """You are GaiaPulse, a household wellness assistant for Diego and Rocío.
Parse the user's natural-language log entry into structured intents.

User resolution rules:
- "Diego" or "I" (when Diego is speaking) → "diego"
- "Rocío", "Rocio", "Roci" → "rocio"
- "We", "both", "nosotros" → "both"
- "I" when Rocío is speaking → "rocio"

Intent types:
- log_meal: eating / food consumed
- log_workout: exercise / gym session
- log_body_metric: weight, body fat, waist measurement, sleep hours
- add_stock: purchased / bought items for the pantry
- consume_stock: used up / consumed items from the pantry
- update_preference: expressing likes, dislikes, restrictions, or impossible activities

Be precise with food quantities and units.  If the user says "around 400 grams each", apply
that to all mentioned food items.  Extract Spanish food names correctly (milanesa, ravioli, etc).
"""


def _build_context_from_layer1(layer1: ParseResult) -> str:
    """Summarise the Layer 1 result so the LLM can build on it."""
    if not layer1.intents:
        return "Layer 1 found no intents."
    lines = [f"Layer 1 confidence: {layer1.overall_confidence:.2f}"]
    for i, intent in enumerate(layer1.intents, 1):
        lines.append(f"  Intent {i}: {intent.intent_type} (conf={intent.confidence:.2f})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _deserialise_intents(raw_intents: list[dict[str, Any]]) -> list[Any]:
    result: list[Any] = []
    for d in raw_intents:
        itype = d.get("intent_type", "mixed")
        # Untrusted LLM JSON, not statically restricted to UserKey; the whole block below
        # is wrapped in try/except and Pydantic validates for real on construction.
        participants = cast(list[UserKey], d.get("participants", ["both"]))
        confidence: float = float(d.get("confidence", 0.5))

        try:
            if itype == "log_meal":
                ipu_raw: dict[str, list[dict]] = d.get("items_per_user", {})
                ipu = {
                    user: [
                        FoodItemRef(
                            # `name` is tolerated because the model still slips
                            # into it now and then; a mismatch here used to raise
                            # ValidationError and silently drop every LLM meal.
                            food_name=item.get("food_name") or item["name"],
                            qty=item.get("qty"),
                            unit=item.get("unit"),
                        )
                        for item in items
                    ]
                    for user, items in ipu_raw.items()
                }
                result.append(
                    MealIntent(
                        participants=participants,
                        confidence=confidence,
                        meal_type=d.get("meal_type", "other"),
                        context=d.get("context", "home"),
                        time_reference=d.get("time_reference"),
                        items_per_user=ipu,
                    )
                )

            elif itype == "log_workout":
                exercises = [
                    ExerciseRef(
                        name=e["name"],
                        muscle_group=e.get("muscle_group"),
                        duration_minutes=e.get("duration_minutes"),
                        sets=e.get("sets"),
                        reps=e.get("reps"),
                    )
                    for e in d.get("exercises", [])
                ]
                result.append(
                    WorkoutIntent(
                        participants=participants,
                        confidence=confidence,
                        workout_type=d.get("workout_type"),
                        duration_minutes=d.get("duration_minutes"),
                        location=d.get("location"),
                        exercises=exercises,
                    )
                )

            elif itype == "log_body_metric":
                result.append(
                    BodyMetricIntent(
                        participants=participants,
                        confidence=confidence,
                        user_key=d.get("user_key", participants[0] if participants else "diego"),
                        weight_kg=d.get("weight_kg"),
                        body_fat_pct=d.get("body_fat_pct"),
                        waist_cm=d.get("waist_cm"),
                        sleep_hours=d.get("sleep_hours"),
                    )
                )

            elif itype == "add_stock":
                items = [
                    StockItemRef(
                        food_name=it["food_name"],
                        quantity=it.get("quantity"),
                        unit=it.get("unit"),
                    )
                    for it in d.get("items", [])
                ]
                result.append(
                    StockAddIntent(
                        participants=participants,
                        confidence=confidence,
                        items=items,
                    )
                )

            elif itype == "consume_stock":
                items = [
                    StockItemRef(
                        food_name=it["food_name"],
                        quantity=it.get("quantity"),
                        unit=it.get("unit"),
                    )
                    for it in d.get("items", [])
                ]
                result.append(
                    StockConsumeIntent(
                        participants=participants,
                        confidence=confidence,
                        items=items,
                    )
                )

            elif itype == "update_preference":
                result.append(
                    PreferenceIntent(
                        participants=participants,
                        confidence=confidence,
                        user_key=d.get("user_key", participants[0] if participants else "both"),
                        item_type=d.get("item_type", "exercise"),
                        item_name=d.get("item_name", ""),
                        preference_signal=d.get("preference_signal", d.get("signal", "dislikes")),
                    )
                )

            else:
                result.append(
                    ParsedIntent(
                        intent_type="mixed",
                        participants=participants,
                        confidence=confidence,
                    )
                )
        except Exception as exc:
            logger.warning("Failed to deserialise intent %r: %s", d, exc)

    return result


# ---------------------------------------------------------------------------
# Adapter implementation
# ---------------------------------------------------------------------------


class OpenAIAdapter(BaseLLMAdapter):
    """Layer 2 parser that calls an OpenAI-compatible chat completion API.

    Compatible with the official OpenAI API as well as any endpoint that
    implements the same interface (e.g. Azure OpenAI, local vLLM servers).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Any | None = None  # lazy init

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI  # type: ignore[import]

                self._client = AsyncOpenAI(
                    api_key=self._settings.openai_api_key,
                    base_url=self._settings.openai_base_url,
                )
            except ImportError as exc:
                raise RuntimeError("openai package is required for the LLM adapter") from exc
        return self._client

    async def parse(
        self,
        text: str,
        speaking_user: str,
        layer1_result: ParseResult,
    ) -> ParseResult:
        """Improve the Layer 1 result using the OpenAI chat completions API."""
        if not self._settings.nlp_enabled:
            logger.debug("LLM parsing disabled (no API key); returning Layer 1 result.")
            return layer1_result

        context_summary = _build_context_from_layer1(layer1_result)
        user_message = (
            f"Speaking user: {speaking_user}\n\n"
            f"Input: {text}\n\n"
            f"Layer 1 parse summary:\n{context_summary}\n\n"
            "Please parse the input and call parse_wellness_input with the result."
        )

        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                tools=[{"type": "function", "function": _PARSE_FUNCTION_SCHEMA}],
                tool_choice={"type": "function", "function": {"name": "parse_wellness_input"}},
                temperature=0.0,
                timeout=15.0,
            )

            tool_call = response.choices[0].message.tool_calls
            if not tool_call:
                logger.warning("LLM returned no tool call; falling back to Layer 1.")
                return layer1_result

            arguments_raw = tool_call[0].function.arguments
            payload = json.loads(arguments_raw)

        except Exception as exc:
            logger.warning("LLM call failed (%s); returning Layer 1 result.", exc)
            return layer1_result

        try:
            raw_intents: list[dict[str, Any]] = payload.get("intents", [])
            overall_confidence: float = float(payload.get("overall_confidence", 0.5))
            parsed_intents = _deserialise_intents(raw_intents)

            if not parsed_intents:
                return layer1_result

            return ParseResult(
                intents=parsed_intents,
                overall_confidence=overall_confidence,
                parser_layer="llm",
                raw_text=text,
            )
        except Exception as exc:
            logger.warning("Failed to build ParseResult from LLM payload (%s); falling back.", exc)
            return layer1_result

    async def health_check(self) -> bool:
        """Attempt a minimal API call to verify connectivity."""
        if not self._settings.nlp_enabled:
            return False
        try:
            client = self._get_client()
            await client.models.list()
            return True
        except Exception:
            return False
