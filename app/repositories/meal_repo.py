from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.repositories.base import BaseRepository


class MealRepository(BaseRepository[MealEvent]):
    def __init__(self, db: Session) -> None:
        super().__init__(MealEvent, db)

    def get_household_meals(
        self,
        household_id: int,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[MealEvent]:
        stmt = (
            select(MealEvent)
            .where(MealEvent.household_id == household_id)
            .options(
                selectinload(MealEvent.participants)
                .selectinload(MealParticipant.items_consumed)
            )
            .order_by(MealEvent.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        if user_id:
            stmt = stmt.join(MealEvent.participants).where(
                MealParticipant.user_id == user_id
            )
        if start_date:
            stmt = stmt.where(MealEvent.timestamp >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            stmt = stmt.where(MealEvent.timestamp <= datetime.combine(end_date, datetime.max.time()))
        return list(self.db.scalars(stmt).unique().all())

    def get_today_meals(self, household_id: int, today: date) -> list[MealEvent]:
        return self.get_household_meals(
            household_id, limit=50, start_date=today, end_date=today
        )

    def get_with_participants(self, meal_id: int) -> MealEvent | None:
        stmt = (
            select(MealEvent)
            .where(MealEvent.id == meal_id)
            .options(
                selectinload(MealEvent.participants)
                .selectinload(MealParticipant.items_consumed)
            )
        )
        return self.db.scalar(stmt)

    def get_recent_foods_for_user(self, user_id: int, limit: int = 30) -> list[str]:
        """Return canonical food names recently consumed by this user."""
        stmt = (
            select(MealItemConsumed.normalized_free_text_name)
            .join(MealItemConsumed.meal_participant)
            .where(MealParticipant.user_id == user_id)
            .order_by(MealItemConsumed.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
