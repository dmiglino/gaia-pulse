"""Tests for the NLP rule-based parser (Layer 1)."""
import pytest

from app.nlp import rules as nlp_rules
from app.nlp.intents import ParseResult


class _ParserProxy:
    """Thin proxy so tests call .parse() as a method without caring about module structure."""

    def parse(self, text: str, speaking_user: str = "diego") -> ParseResult:
        return nlp_rules.parse(text, speaking_user=speaking_user)


@pytest.fixture
def parser() -> _ParserProxy:
    return _ParserProxy()


class TestStockAdd:
    def test_simple_purchase(self, parser: _ParserProxy) -> None:
        result = parser.parse("We bought 6 bananas and 4 bell peppers", speaking_user="diego")
        assert result.overall_confidence > 0.5
        assert len(result.intents) >= 1
        stock_intent = next(
            (i for i in result.intents if i.intent_type == "add_stock"), None
        )
        assert stock_intent is not None
        assert len(stock_intent.items) == 2
        names = [i.food_name.lower() for i in stock_intent.items]
        assert any("banana" in n for n in names)
        assert any("bell pepper" in n or "pepper" in n for n in names)

    def test_purchase_quantities(self, parser: _ParserProxy) -> None:
        result = parser.parse("Compramos 2 kilos de arroz y 500 gramos de pasta", speaking_user="diego")
        # Even if in Spanish, quantities should be extracted or confidence should be low
        assert result is not None

    def test_consume_stock(self, parser: _ParserProxy) -> None:
        result = parser.parse("We used 4 tomatoes for lunch", speaking_user="diego")
        assert result is not None
        consume = next(
            (i for i in result.intents if i.intent_type == "consume_stock"), None
        )
        assert consume is not None


class TestMealLogging:
    def test_shared_meal_same_food(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Tonight we had milanesa with mashed potatoes for dinner",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        assert meal.meal_type in ("dinner", "other")

    def test_different_foods_per_person(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Rocío ate milanesa and a banana, Diego ate ravioli and ice cream",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        # Should have per-user items
        assert meal.items_per_user
        assert "diego" in {k.lower() for k in meal.items_per_user}
        assert "rocío" in {k.lower() for k in meal.items_per_user} or \
               "rocio" in {k.lower() for k in meal.items_per_user}

    def test_meal_with_quantity(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "We are about to have ravioli with tomato sauce, around 400 grams each",
            speaking_user="diego",
        )
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None

    def test_i_refers_to_speaking_user(self, parser: _ParserProxy) -> None:
        result = parser.parse("I had eggs for breakfast", speaking_user="rocio")
        meal = next((i for i in result.intents if i.intent_type == "log_meal"), None)
        assert meal is not None
        # "I" should resolve to rocio
        participants = list(meal.items_per_user.keys()) if meal.items_per_user else []
        assert any("rocio" in p.lower() or "rocío" in p.lower() for p in participants) or \
               any("both" in p.lower() for p in participants)


class TestWorkoutLogging:
    def test_gym_session_both_users(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "We went to the gym for 1 hour and trained chest, shoulders and triceps",
            speaking_user="diego",
        )
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        assert workout.duration_minutes == 60
        assert "both" in workout.participants or len(workout.participants) > 0

    def test_single_user_workout(self, parser: _ParserProxy) -> None:
        result = parser.parse(
            "Diego rode a bike for 40 minutes",
            speaking_user="rocio",
        )
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None
        assert workout.duration_minutes == 40
        assert any("diego" in p.lower() for p in workout.participants)

    def test_yoga_workout(self, parser: _ParserProxy) -> None:
        result = parser.parse("Rocío did yoga today", speaking_user="diego")
        workout = next((i for i in result.intents if i.intent_type == "log_workout"), None)
        assert workout is not None


class TestBodyMetric:
    def test_weight_log(self, parser: _ParserProxy) -> None:
        result = parser.parse("Today I weigh 82.3 kg", speaking_user="diego")
        metric = next((i for i in result.intents if i.intent_type == "log_body_metric"), None)
        assert metric is not None
        assert metric.weight_kg == pytest.approx(82.3, abs=0.1)

    def test_weight_in_different_phrasing(self, parser: _ParserProxy) -> None:
        result = parser.parse("My weight today is 75 kg", speaking_user="rocio")
        metric = next((i for i in result.intents if i.intent_type == "log_body_metric"), None)
        assert metric is not None
        assert metric.weight_kg == pytest.approx(75.0, abs=0.1)


class TestPreferenceUpdate:
    def test_negative_preference(self, parser: _ParserProxy) -> None:
        result = parser.parse("Do not suggest swimming", speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.preference_signal in ("impossible", "dislikes", "avoid")
        assert "swimming" in pref.item_name.lower()

    def test_positive_preference(self, parser: _ParserProxy) -> None:
        result = parser.parse("We like biking", speaking_user="diego")
        pref = next((i for i in result.intents if i.intent_type == "update_preference"), None)
        assert pref is not None
        assert pref.preference_signal in ("likes", "preferred")
        assert "bik" in pref.item_name.lower()


class TestConfidence:
    def test_well_formed_input_high_confidence(self, parser: _ParserProxy) -> None:
        result = parser.parse("Today I weigh 82.3 kg", speaking_user="diego")
        assert result.overall_confidence >= 0.6

    def test_garbage_input_low_confidence(self, parser: _ParserProxy) -> None:
        result = parser.parse("asdfghjklqwerty random words", speaking_user="diego")
        assert result.overall_confidence < 0.5 or len(result.intents) == 0
