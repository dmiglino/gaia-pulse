from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import local_day_bounds
from app.models.workout import (
    ExerciseType,
    WorkoutExercise,
    WorkoutParticipant,
    WorkoutSession,
)
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

    def get_last_session_start(self, user_id: int, household_id: int) -> datetime | None:
        """Cuándo entrenó por última vez, o `None` si nunca.

        `get_user_recent_sessions` responde "¿entrenó en los últimos N días?", que
        alcanza para decidir si avisar pero no para decir **cuánto** hace: el job de
        inactividad necesita el número para escalar (avisar de nuevo a los 8 días
        después de haber avisado a los 4) en vez de repetir el mismo aviso. Un
        `max()` en la base en lugar de traer las sesiones con sus ejercicios para
        mirarles la fecha.
        """
        stmt = (
            select(func.max(WorkoutSession.timestamp_start))
            .join(WorkoutSession.participants)
            .where(
                and_(
                    WorkoutSession.household_id == household_id,
                    WorkoutParticipant.user_id == user_id,
                )
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

    def get_last_trained_at_by_muscle_group(
        self, user_id: int, household_id: int
    ) -> dict[str, datetime]:
        """Cuándo se estimuló por última vez **cada** grupo muscular.

        `get_user_muscle_groups_trained` cuenta cuántas veces en una ventana fija, que
        responde "¿qué entrena?" y no "¿qué le toca hoy?". Una ventana de recuperación
        necesita el instante: pecho hace un día está en descanso, pecho hace nueve está
        atrasado, y contar dos veces cada uno no distingue esos dos casos.

        Sin ventana a propósito: un grupo que no aparece es un grupo que **nunca** se
        entrenó, y eso es información distinta de "hace mucho". Un `GROUP BY` con
        `max()` en la base y no las sesiones con sus ejercicios: son tantas filas como
        grupos musculares, no como ejercicios.
        """
        stmt = (
            select(WorkoutExercise.muscle_group, func.max(WorkoutSession.timestamp_start))
            .join(
                WorkoutParticipant,
                WorkoutParticipant.id == WorkoutExercise.workout_participant_id,
            )
            .join(WorkoutSession, WorkoutSession.id == WorkoutParticipant.workout_session_id)
            .where(
                and_(
                    WorkoutSession.household_id == household_id,
                    WorkoutParticipant.user_id == user_id,
                    WorkoutExercise.muscle_group.is_not(None),
                )
            )
            .group_by(WorkoutExercise.muscle_group)
        )
        #: El grupo se normaliza a minúsculas acá porque quien escribe la columna es el
        #: NLP y no hay `CHECK`: "Chest" y "chest" son dos claves distintas para el
        #: mismo músculo, y la ventana de recuperación se aplicaría dos veces.
        out: dict[str, datetime] = {}
        for group, last in self.db.execute(stmt).all():
            if group is None or last is None:
                continue
            key = group.strip().lower()
            if not key:
                continue
            previous = out.get(key)
            if previous is None or last > previous:
                out[key] = last
        return out

    def get_recent_perceived_effort(
        self, user_id: int, household_id: int, days: int = 14
    ) -> list[int]:
        """Los RPE anotados en la ventana, del más nuevo al más viejo.

        `perceived_effort` es 1-10 y opcional, así que los `NULL` se filtran en la base
        en vez de contarse como cero: un ejercicio sin esfuerzo anotado no es un
        ejercicio suave, y promediarlo como 0 hace que quien no completa el campo
        parezca no estar esforzándose.

        Devuelve la lista y no el promedio: promediar es una decisión —¿de la sesión o
        del ejercicio?— y las decisiones no son del repositorio.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(WorkoutExercise.perceived_effort)
            .join(
                WorkoutParticipant,
                WorkoutParticipant.id == WorkoutExercise.workout_participant_id,
            )
            .join(WorkoutSession, WorkoutSession.id == WorkoutParticipant.workout_session_id)
            .where(
                and_(
                    WorkoutSession.household_id == household_id,
                    WorkoutParticipant.user_id == user_id,
                    WorkoutSession.timestamp_start >= cutoff,
                    WorkoutExercise.perceived_effort.is_not(None),
                )
            )
            .order_by(WorkoutSession.timestamp_start.desc())
        )
        return [int(v) for v in self.db.scalars(stmt).all() if v is not None]


class ExerciseTypeRepository(BaseRepository[ExerciseType]):
    """El catálogo de ejercicios.

    Vive acá y no en su propio archivo porque es una tabla de catálogo con un solo
    lector: un repositorio de un método en un módulo aparte es una capa que no compra
    nada. `seed.py:160-180` la siembra con 20 filas y `docker-compose.yml` corre el seed
    al arrancar, así que en producción tiene contenido; en los tests **está vacía**,
    porque ningún fixture la llena, y quien la lea tiene que funcionar con cero filas.
    """

    def __init__(self, db: Session) -> None:
        super().__init__(ExerciseType, db)

    def list_all(self) -> list[ExerciseType]:
        """Todo el catálogo, en orden estable por nombre.

        El orden importa porque quien elige una actividad de acá desempata por
        posición, y un orden que decide el motor hace que la misma persona con los
        mismos datos reciba distintas sugerencias entre corridas.
        """
        return list(self.db.scalars(select(ExerciseType).order_by(ExerciseType.name)).all())
