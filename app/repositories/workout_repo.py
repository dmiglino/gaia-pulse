from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import local_day_bounds
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession
from app.repositories.base import BaseRepository


class WorkoutRepository(BaseRepository[WorkoutSession]):
    def __init__(self, db: Session) -> None:
        super().__init__(WorkoutSession, db)

    def get_household_sessions(
        self,
        household_id: int,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[WorkoutSession]:
        stmt = (
            select(WorkoutSession)
            .where(WorkoutSession.household_id == household_id)
            .options(
                selectinload(WorkoutSession.participants).selectinload(WorkoutParticipant.exercises)
            )
            .order_by(WorkoutSession.timestamp_start.desc())
            .limit(limit)
            .offset(offset)
        )
        if user_id:
            stmt = stmt.join(WorkoutSession.participants).where(
                WorkoutParticipant.user_id == user_id
            )
        #: Días **locales** convertidos a UTC: ver la nota en `local_day_bounds`.
        if start_date:
            stmt = stmt.where(WorkoutSession.timestamp_start >= local_day_bounds(start_date)[0])
        # `end_date` faltaba, así que el único filtro posible era "desde tal día en
        # adelante": la pantalla de entrenamientos no tenía forma de pedir un día solo.
        if end_date:
            stmt = stmt.where(WorkoutSession.timestamp_start <= local_day_bounds(end_date)[1])
        return list(self.db.scalars(stmt).unique().all())

    def get_today_sessions(self, household_id: int, today: date) -> list[WorkoutSession]:
        #: Con `end_date`, "hoy" es hoy: antes era "desde el arranque de hoy en
        #: adelante", así que una sesión con fecha futura contaba como de hoy.
        return self.get_household_sessions(household_id, limit=20, start_date=today, end_date=today)

    def get_with_details(self, session_id: int) -> WorkoutSession | None:
        stmt = (
            select(WorkoutSession)
            .where(WorkoutSession.id == session_id)
            .options(
                selectinload(WorkoutSession.participants).selectinload(WorkoutParticipant.exercises)
            )
        )
        return self.db.scalar(stmt)

    def get_user_recent_sessions(
        self, user_id: int, household_id: int, days: int = 30
    ) -> list[WorkoutSession]:
        #: Aware: ver la nota en `BodyMetricRepository.get_weight_series`. Acá el
        #: corrimiento decidía si el job de inactividad avisaba o no.
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(WorkoutSession)
            .join(WorkoutSession.participants)
            .where(
                and_(
                    WorkoutSession.household_id == household_id,
                    WorkoutParticipant.user_id == user_id,
                    WorkoutSession.timestamp_start >= cutoff,
                )
            )
            .options(
                selectinload(WorkoutSession.participants).selectinload(WorkoutParticipant.exercises)
            )
            .order_by(WorkoutSession.timestamp_start.desc())
        )
        return list(self.db.scalars(stmt).unique().all())

    def get_user_muscle_groups_trained(
        self, user_id: int, household_id: int, days: int = 30
    ) -> dict[str, int]:
        """Return counts of muscle groups trained per user in last N days."""
        sessions = self.get_user_recent_sessions(user_id, household_id, days)
        counts: dict[str, int] = {}
        for session in sessions:
            for p in session.participants:
                if p.user_id != user_id:
                    continue
                for ex in p.exercises:
                    mg = ex.muscle_group or "other"
                    counts[mg] = counts.get(mg, 0) + 1
        return counts
