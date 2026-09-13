from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.clock import local_day_bounds
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
                selectinload(MealEvent.participants).selectinload(MealParticipant.items_consumed)
            )
            .order_by(MealEvent.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        if user_id:
            stmt = stmt.join(MealEvent.participants).where(MealParticipant.user_id == user_id)
        #: Los límites son días **locales** convertidos a UTC. Con
        #: `datetime.combine(...)` naive, "hoy" era el día UTC y una comida de las
        #: 22:00 de acá aparecía en el día siguiente.
        if start_date:
            stmt = stmt.where(MealEvent.timestamp >= local_day_bounds(start_date)[0])
        if end_date:
            stmt = stmt.where(MealEvent.timestamp <= local_day_bounds(end_date)[1])
        return list(self.db.scalars(stmt).unique().all())

    def get_today_meals(self, household_id: int, today: date) -> list[MealEvent]:
        return self.get_household_meals(household_id, limit=50, start_date=today, end_date=today)

    def get_last_meal_at(self, user_id: int, household_id: int) -> datetime | None:
        """Cuándo comió por última vez **según el registro**, o `None` si nunca anotó.

        La comida es del hogar y el participante es quien la comió, así que el filtro va
        por los dos, igual que en `WorkoutRepository.get_last_session_start`: sin la
        mitad del participante, la cena que Rocío anotó sola contaría como registro de
        Diego y el hueco de él quedaría tapado.

        Un `max()` en la base y no la lista de comidas con sus participantes e ítems: el
        job de ausencia solo necesita el instante.
        """
        stmt = (
            select(func.max(MealEvent.timestamp))
            .join(MealEvent.participants)
            .where(
                and_(
                    MealEvent.household_id == household_id,
                    MealParticipant.user_id == user_id,
                )
            )
        )
        return self.db.scalar(stmt)

    def get_with_participants(self, meal_id: int) -> MealEvent | None:
        stmt = (
            select(MealEvent)
            .where(MealEvent.id == meal_id)
            .options(
                selectinload(MealEvent.participants).selectinload(MealParticipant.items_consumed)
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
