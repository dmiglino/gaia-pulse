from datetime import date

from sqlalchemy.orm import Session

from app.core.clock import as_utc, local_today
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession
from app.recommendations import learning
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.workout import WorkoutSessionCreate

#: A partir de este esfuerzo percibido (RPE 1-10) lo hecho pesa la mitad como gusto.
#: Haber hecho algo es evidencia de que se puede repetir, y una serie al 9 o al 10 dice
#: justamente lo contrario: se hizo, costó, y no es lo que se va a elegir el martes que
#: viene. Un RPE bajo no se castiga —una sesión liviana es perfectamente repetible—, así
#: que la escala es de un solo lado a propósito.
_HIGH_EFFORT_RPE = 9
_HIGH_EFFORT_WEIGHT = 0.5


class WorkoutService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.workout_repo = WorkoutRepository(db)

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

                #: Lo que se hizo, ejercicio por ejercicio. Antes la única señal de un
                #: entrenamiento era su `workout_type` —"gym", "home", "outdoor"—, que es
                #: el lugar, no la actividad: registrar cien sesiones de gimnasio no
                #: enseñaba nada sobre press de banca ni sobre correr. El generador de
                #: actividad propone candidatos con `subject_type="exercise"`, así que
                #: estas filas sí las lee alguien.
                learning.record_signal(
                    self.db,
                    user_id=p_data.user_id,
                    signal_type="repeated_activity",
                    subject_type="exercise",
                    subject_name=ex_data.exercise_name,
                    value=self._effort_weight(ex_data.perceived_effort),
                    source_type="implicit",
                    source_entity_type="workout_session",
                    source_entity_id=session.id,
                )
                #: Y el grupo muscular, que es el sujeto de la rotación que propone
                #: `activity_generator`. Sale de la columna de la captura y no del
                #: catálogo: cuando el ejercicio viene sin grupo no se inventa uno.
                #:
                #: Resolverlo contra `ExerciseType` —lo que la 4.5 daba por hecho— **no se
                #: hace**, y la razón es de datos y no de alcance: el catálogo está en inglés
                #: ("Bench Press", "Cycling") mientras las capturas de esta casa están en
                #: castellano ("press de banca", "bicicleta"), y `ExerciseType` no tiene
                #: `aliases_json` donde poner las dos formas —`FoodItem` sí, que es por qué del
                #: lado de la comida el catálogo resuelve—. Sin esa columna el match por nombre
                #: no acertaría casi nunca, y agregarla es una migración que v3 no tiene.
                #: `docs/v3-plan.md` lo deja anotado como el pendiente que es.
                #:
                #: Lo que **sí** conforma es el vocabulario: el grupo pasa por
                #: `normalize_muscle_group`, así que un "triceps" dicho en una captura y el
                #: "arms" que escribe el catálogo son el mismo sujeto y no dos. Sin esto la
                #: señal y el candidato tenían claves distintas y ninguno veía al otro.
                #: La columna `WorkoutExercise.muscle_group` queda como se capturó, porque es
                #: lo que la pantalla de entrenamientos muestra.
                if ex_data.muscle_group:
                    learning.record_signal(
                        self.db,
                        user_id=p_data.user_id,
                        signal_type="repeated_activity",
                        subject_type="muscle_group",
                        subject_name=learning.normalize_muscle_group(ex_data.muscle_group),
                        value=self._effort_weight(ex_data.perceived_effort),
                        source_type="implicit",
                        source_entity_type="workout_session",
                        source_entity_id=session.id,
                    )

            # Record implicit signals for the activity
            if data.workout_type:
                learning.record_signal(
                    self.db,
                    user_id=p_data.user_id,
                    signal_type="repeated_activity",
                    subject_type="exercise",
                    subject_name=data.workout_type,
                    value=1.0,
                    source_type="implicit",
                    source_entity_type="workout_session",
                    source_entity_id=session.id,
                )

        self.db.flush()
        self.db.commit()
        self.db.refresh(session)
        return session

    @staticmethod
    def _effort_weight(perceived_effort: int | None) -> float:
        """Cuánto vale como gusto haber hecho un ejercicio con este RPE."""
        if perceived_effort is not None and perceived_effort >= _HIGH_EFFORT_RPE:
            return _HIGH_EFFORT_WEIGHT
        return 1.0

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
