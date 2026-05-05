from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.repositories.food_repo import FoodRepository
from app.repositories.meal_repo import MealRepository
from app.repositories.suggestion_repo import BehaviorSignalRepository
from app.schemas.meal import MealEventCreate


class MealService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.meal_repo = MealRepository(db)
        self.food_repo = FoodRepository(db)
        self.signal_repo = BehaviorSignalRepository(db)

    def log_meal(self, household_id: int, data: MealEventCreate) -> MealEvent:
        """Create a full meal event with per-user participants and consumed items."""
        event = MealEvent(
            household_id=household_id,
            timestamp=data.timestamp,
            meal_type=data.meal_type,
            context=data.context,
            notes=data.notes,
        )
        self.db.add(event)
        self.db.flush()

        for p_data in data.participants:
            participant = MealParticipant(
                meal_event_id=event.id,
                user_id=p_data.user_id,
                portion_label=p_data.portion_label,
                estimated_total_grams=p_data.estimated_total_grams,
                hunger_before=p_data.hunger_before,
                satiety_after=p_data.satiety_after,
                notes=p_data.notes,
            )
            self.db.add(participant)
            self.db.flush()

            for item_data in p_data.items:
                # Attempt to link to known FoodItem
                food_item = self.food_repo.find_by_name(item_data.food_name)
                item = MealItemConsumed(
                    meal_event_id=event.id,
                    meal_participant_id=participant.id,
                    food_item_id=food_item.id if food_item else None,
                    normalized_free_text_name=item_data.food_name.strip().lower(),
                    quantity=item_data.quantity,
                    unit=item_data.unit,
                    estimated_grams=item_data.estimated_grams,
                    preparation=item_data.preparation,
                    affects_stock=item_data.affects_stock,
                    notes=None,
                )
                self.db.add(item)

            # Record implicit behavior signals for each food eaten
            for item_data in p_data.items:
                self.signal_repo.record(
                    user_id=p_data.user_id,
                    signal_type="repeated_meal_choice",
                    entity_type="food",
                    entity_name=item_data.food_name,
                    value=1.0,
                    source_type="implicit",
                    source_entity_type="meal_event",
                    source_entity_id=event.id,
                )

        self.db.flush()
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_meals(
        self,
        household_id: int,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
    ) -> list[MealEvent]:
        return self.meal_repo.get_household_meals(
            household_id, limit=limit, offset=offset, user_id=user_id
        )

    def get_today_meals(self, household_id: int) -> list[MealEvent]:
        from datetime import date
        return self.meal_repo.get_today_meals(household_id, date.today())

    def get_meal(self, meal_id: int) -> MealEvent | None:
        return self.meal_repo.get_with_participants(meal_id)

    def delete_meal(self, meal_id: int) -> bool:
        meal = self.meal_repo.get(meal_id)
        if not meal:
            return False
        self.meal_repo.delete(meal)
        self.db.commit()
        return True
