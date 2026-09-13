"""Tests for the OpenAI adapter's deserialisation of the tool-call payload.

No network: `_deserialise_intents` is a pure function over the parsed JSON.
"""

from typing import Any

from app.nlp.adapters.openai_adapter import _PARSE_FUNCTION_SCHEMA, _deserialise_intents


def _meal_payload(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "intent_type": "log_meal",
            "participants": ["diego"],
            "confidence": 0.9,
            "meal_type": "lunch",
            "items_per_user": {"diego": [item]},
        }
    ]


def test_meal_items_deserialise() -> None:
    """`FoodItemRef` declares `food_name`; building it with `name=` raised
    ValidationError, so every LLM-parsed meal was silently dropped."""
    intents = _deserialise_intents(
        _meal_payload({"food_name": "milanesa", "qty": 200, "unit": "g"})
    )
    assert len(intents) == 1
    items = intents[0].items_per_user["diego"]
    assert items[0].food_name == "milanesa"
    assert items[0].qty == 200
    assert items[0].unit == "g"


def test_meal_items_tolerate_the_old_key() -> None:
    """The model sometimes answers `name` anyway; that must not drop the meal."""
    intents = _deserialise_intents(_meal_payload({"name": "ravioli"}))
    assert len(intents) == 1
    assert intents[0].items_per_user["diego"][0].food_name == "ravioli"


def test_schema_asks_for_the_field_the_model_declares() -> None:
    """The prompt schema and `FoodItemRef` must use one vocabulary."""
    props = _PARSE_FUNCTION_SCHEMA["parameters"]["properties"]["intents"]["items"]["properties"]
    meal_item = props["items_per_user"]["additionalProperties"]["items"]
    assert "food_name" in meal_item["properties"]
    assert meal_item["required"] == ["food_name"]
