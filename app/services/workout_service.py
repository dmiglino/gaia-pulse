from datetime import date

from sqlalchemy.orm import Session

from app.core.clock import as_utc, local_today
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession
from app.repositories.suggestion_repo import BehaviorSignalRepository
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.workout import WorkoutSessionCreate


class WorkoutService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.workout_repo = WorkoutRepository(db)
        self.signal_repo = BehaviorSignalRepository(db)

    def log_workout(self, household_id: int, data: WorkoutSessionCreate) -> WorkoutSession:
        """Create a workout session with per-user participants and exercises."""
        session = WorkoutSession(
            household_id=household_id,
            #: Como en `MealService.log_meal`: la columna es UTC y de eso dependen los
            #: límites del día local.
            timestamp_start=as_utc(data.timestamp_start),
            duration_minutes=data.duration_minutes,
            workout_type=data.workout_type,
            location=data.location,
            calories_estimated=data.calories_estimated,
            source=data.source,
            notes=data.notes,
        )
        self.db.add(session)
        self.db.flush()

        for p_data in data.participants:
            participant = WorkoutParticipant(
                workout_session_id=session.id,
                user_id=p_data.user_id,
                notes=p_data.notes,
            )
            self.db.add(participant)
            self.db.flush()

            for ex_data in p_data.exercises:
                exercise = WorkoutExercise(
                    workout_session_id=session.id,
                    workout_participant_id=participant.id,
                    exercise_name=ex_data.exercise_name,
                    muscle_group=ex_data.muscle_group,
                    sets=ex_data.sets,
                    reps=ex_data.reps,
                    load_kg=ex_data.load_kg,
                    duration_minutes=ex_data.duration_minutes,
                    distance_km=ex_data.distance_km,
                    perceived_effort=ex_data.perceived_effort,
                    notes=ex_data.notes,
                )
                self.db.add(exercise)

            # Record implicit signals for the activity
            if data.workout_type:
                self.signal_repo.record(
                    user_id=p_data.user_id,
                    signal_type="repeated_activity",
                    entity_type="exercise",
                    entity_name=data.workout_type,
                    value=1.0,
                    source_type="implicit",
                    source_entity_type="workout_session",
                    source_entity_id=session.id,
                )

        self.db.flush()
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_sessions(
        self,
        household_id: int,
        limit: int = 20,
        offset: int = 0,
        user_id: int | None = None,
        on_date: date | None = None,
    ) -> list[WorkoutSession]:
        """Los entrenamientos del hogar, del más nuevo al más viejo.

        `on_date` filtra un solo día. El filtro de fecha de la pantalla mandaba un
        parámetro que la ruta no leía, así que elegir un día no cambiaba nada.
        """
        return self.workout_repo.get_household_sessions(
            household_id,
            limit=limit,
            offset=offset,
            user_id=user_id,
            start_date=on_date,
            end_date=on_date,
        )

    def get_today_sessions(self, household_id: int) -> list[WorkoutSession]:
        #: `date.today()` es el día del reloj del proceso — UTC en el contenedor —,
        #: así que entre las 21:00 y la medianoche local "hoy" era mañana.
        return self.workout_repo.get_today_sessions(household_id, local_today())

    def get_session(self, session_id: int) -> WorkoutSession | None:
        return self.workout_repo.get_with_details(session_id)

    def delete_session(self, session_id: int) -> bool:
        session = self.workout_repo.get(session_id)
        if not session:
            return False
        self.workout_repo.delete(session)
        self.db.commit()
        return True

    def get_user_recent_sessions(
        self, user_id: int, household_id: int, days: int = 30
    ) -> list[WorkoutSession]:
        return self.workout_repo.get_user_recent_sessions(user_id, household_id, days)
