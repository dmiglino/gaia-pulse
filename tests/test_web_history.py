"""Page tests for /history — one per tab, with a row of data in each."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.meal import MealEvent
from app.models.pantry import PantryMovement, PantryStock
from app.models.user import User
from app.models.workout import WorkoutSession


def test_history_tabs_render_their_rows(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    now = datetime.now(UTC)

    db.add(
        MealEvent(
            household_id=diego.household_id,
            timestamp=now - timedelta(hours=2),
            meal_type="lunch",
            notes="Grilled chicken with rice",
        )
    )
    db.add(
        WorkoutSession(
            household_id=diego.household_id,
            timestamp_start=now - timedelta(hours=5),
            duration_minutes=45,
            workout_type="gym",
            source="manual",
        )
    )
    db.add(
        BodyMetricLog(
            user_id=diego.id,
            timestamp=now - timedelta(days=1),
            weight_kg=78.4,
        )
    )
    stock = PantryStock(
        household_id=diego.household_id,
        food_item_id=banana.id,
        current_quantity=4.0,
        unit="unit",
    )
    db.add(stock)
    db.flush()
    db.add(
        PantryMovement(
            household_id=diego.household_id,
            food_item_id=banana.id,
            user_id=diego.id,
            movement_type="purchase",
            quantity=4.0,
            unit="unit",
            timestamp=now - timedelta(hours=6),
        )
    )
    db.flush()

    # The workouts tab used to render empty cards: the partial reads `workout`
    # while the history loop variable is `event`.
    expected = {
        "meals": "Grilled chicken with rice",
        "workouts": "45",
        "body_metrics": "78.4",
        "pantry": "banana",
    }
    for tab, needle in expected.items():
        r = authenticated_client.get(f"/history/?tab={tab}")
        assert r.status_code == 200, (tab, r.text[:500])
        assert needle in r.text, (tab, needle)
