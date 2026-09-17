"""Tests for meal logging."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.user import User
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.services.meal_service import MealService


def now() -> datetime:
    return datetime.now(UTC)


class TestMealLogging:
    def test_log_single_user_meal(self, db: Session, household: Household, diego: User) -> None:
        svc = MealService(db)
        data = MealEventCreate(
            timestamp=now(),
            meal_type="dinner",
            context="home",
            participants=[
                MealParticipantCreate(
                    user_id=diego.id,
                    items=[
                        MealItemCreate(food_name="milanesa", quantity=1, unit="serving"),
                        MealItemCreate(food_name="mashed potatoes", quantity=300, unit="g"),
                    ],
                )
            ],
        )
        event = svc.log_meal(household.id, data)
        assert event.id is not None
        assert event.meal_type == "dinner"

        # Reload with participants
        loaded = svc.get_meal(event.id)
        assert loaded is not None
        assert len(loaded.participants) == 1
        assert loaded.participants[0].user_id == diego.id
        assert len(loaded.participants[0].items_consumed) == 2

    def test_log_shared_meal_different_foods(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        svc = MealService(db)
        data = MealEventCreate(
            timestamp=now(),
            meal_type="dinner",
            context="home",
            participants=[
                MealParticipantCreate(
                    user_id=diego.id,
                    items=[
                        MealItemCreate(food_name="ravioli"),
                        MealItemCreate(food_name="ice cream"),
                    ],
                ),
                MealParticipantCreate(
                    user_id=rocio.id,
                    items=[
                        MealItemCreate(food_name="milanesa"),
                        MealItemCreate(food_name="banana"),
                    ],
                ),
            ],
        )
        event = svc.log_meal(household.id, data)
        loaded = svc.get_meal(event.id)
        assert len(loaded.participants) == 2

        diego_part = next(p for p in loaded.participants if p.user_id == diego.id)
        rocio_part = next(p for p in loaded.participants if p.user_id == rocio.id)

        diego_names = {i.normalized_free_text_name for i in diego_part.items_consumed}
        rocio_names = {i.normalized_free_text_name for i in rocio_part.items_consumed}

        assert "ravioli" in diego_names
        assert "milanesa" in rocio_names
        assert "milanesa" not in diego_names  # Critical: per-user isolation
        assert "ravioli" not in rocio_names  # Critical: per-user isolation

    def test_meal_isolation_between_users(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Ensure one user's meal data does not bleed into another's."""
        svc = MealService(db)

        # Diego eats alone
        data = MealEventCreate(
            timestamp=now(),
            meal_type="lunch",
            context="outside",
            participants=[
                MealParticipantCreate(user_id=diego.id, items=[MealItemCreate(food_name="burger")])
            ],
        )
        event = svc.log_meal(household.id, data)
        loaded = svc.get_meal(event.id)

        # Only one participant — Rocío should NOT be in this meal
        assert len(loaded.participants) == 1
        assert loaded.participants[0].user_id == diego.id
        # Rocío has no participation
        assert not any(p.user_id == rocio.id for p in loaded.participants)

    def test_delete_meal(self, db: Session, household: Household, diego: User) -> None:
        svc = MealService(db)
        data = MealEventCreate(
            timestamp=now(),
            meal_type="breakfast",
            context="home",
            participants=[
                MealParticipantCreate(user_id=diego.id, items=[MealItemCreate(food_name="oats")])
            ],
        )
        event = svc.log_meal(household.id, data)
        assert svc.delete_meal(event.id) is True
        assert svc.get_meal(event.id) is None
