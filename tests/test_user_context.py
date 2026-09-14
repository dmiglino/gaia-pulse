"""Lo que la app sabe de una persona, leído una sola vez (4.5.1).

Dos cosas se prueban acá y son distintas. Una es cada consulta nueva: los repositorios
que el contexto necesitaba y que antes eran consultas escritas a mano dentro de los
generadores. La otra es el armado en sí, que es donde vive el riesgo interesante — una
cuenta sin datos tiene que dar un contexto válido y lleno de ausencias, y el día tiene
que ser el local, porque las dos veces que este repo tuvo un bug de fecha fue por
mezclar el día UTC con el día de la casa.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.config import get_settings
from app.models.blood_analysis import BloodAnalysis
from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.household import Household
from app.models.meal import MealEvent, MealItemConsumed, MealParticipant
from app.models.pantry import PantryStock
from app.models.suggestion import Suggestion
from app.models.user import User
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession
from app.recommendations.context import (
    MacroTotals,
    _macro_totals,
    build_user_context,
)
from app.repositories.blood_repo import BloodAnalysisRepository
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.meal_repo import MealRepository
from app.repositories.pantry_repo import PantryStockRepository
from app.repositories.suggestion_repo import SuggestionRepository
from app.repositories.workout_repo import WorkoutRepository


def _local_noon_today() -> datetime:
    """Mediodía local de hoy, para las comidas que tienen que caer en "hoy".

    `datetime.now(UTC) - 2h` parece equivalente y no lo es: entre las 00:00 y las 02:00
    locales ese instante es de ayer, y el test que mira los macros de hoy fallaría una
    vez cada doce corridas nocturnas. El mediodía no cruza ningún borde.
    """
    return datetime.combine(local_today(), time(12, 0), tzinfo=ZoneInfo(get_settings().timezone))


def _workout(
    db: Session,
    user: User,
    *,
    when: datetime,
    exercises: list[tuple[str | None, int | None]],
) -> WorkoutSession:
    """Una sesión con sus ejercicios: cada uno es (grupo muscular, RPE)."""
    session = WorkoutSession(
        household_id=user.household_id, workout_type="gym", timestamp_start=when
    )
    db.add(session)
    db.flush()
    participant = WorkoutParticipant(workout_session_id=session.id, user_id=user.id)
    db.add(participant)
    db.flush()
    for muscle_group, effort in exercises:
        db.add(
            WorkoutExercise(
                workout_session_id=session.id,
                workout_participant_id=participant.id,
                exercise_name=muscle_group or "algo",
                muscle_group=muscle_group,
                perceived_effort=effort,
            )
        )
    db.flush()
    return session


def _meal(
    db: Session,
    user: User,
    *,
    when: datetime,
    items: list[tuple[str, FoodItem | None, float | None, str | None]],
) -> MealEvent:
    """Una comida con sus ítems: (nombre libre, alimento del catálogo, cantidad, unidad)."""
    event = MealEvent(household_id=user.household_id, timestamp=when, meal_type="dinner")
    db.add(event)
    db.flush()
    participant = MealParticipant(meal_event_id=event.id, user_id=user.id)
    db.add(participant)
    db.flush()
    for name, food, quantity, unit in items:
        db.add(
            MealItemConsumed(
                meal_event_id=event.id,
                meal_participant_id=participant.id,
                food_item_id=food.id if food is not None else None,
                normalized_free_text_name=name,
                quantity=quantity,
                unit=unit,
            )
        )
    db.flush()
    return event


class TestMuscleGroupRecency:
    """`get_last_trained_at_by_muscle_group`: el instante por grupo, no el conteo."""

    def test_each_group_carries_its_own_last_stimulus(self, db: Session, diego: User) -> None:
        now = datetime.now(UTC)
        _workout(db, diego, when=now - timedelta(days=9), exercises=[("chest", None)])
        _workout(db, diego, when=now - timedelta(days=1), exercises=[("legs", None)])

        last = WorkoutRepository(db).get_last_trained_at_by_muscle_group(
            diego.id, diego.household_id
        )

        assert set(last) == {"chest", "legs"}
        assert last["legs"] > last["chest"]

    def test_the_group_is_lowercased_because_the_nlp_writes_the_column(
        self, db: Session, diego: User
    ) -> None:
        """Sin `CHECK` en la columna, "Chest" y "chest" llegan las dos.

        Si se contaran como dos claves, la ventana de recuperación del pecho se
        evaluaría dos veces y la más vieja diría que está descansado.
        """
        now = datetime.now(UTC)
        _workout(db, diego, when=now - timedelta(days=6), exercises=[("Chest", None)])
        _workout(db, diego, when=now - timedelta(days=2), exercises=[("  chest ", None)])

        last = WorkoutRepository(db).get_last_trained_at_by_muscle_group(
            diego.id, diego.household_id
        )

        assert set(last) == {"chest"}
        assert (datetime.now(UTC) - last["chest"].replace(tzinfo=UTC)).days == 2

    def test_a_group_never_trained_is_absent_not_old(self, db: Session, diego: User) -> None:
        """Ausente y "hace mucho" son datos distintos: uno propone empezar, el otro volver."""
        _workout(db, diego, when=datetime.now(UTC), exercises=[("core", None)])

        last = WorkoutRepository(db).get_last_trained_at_by_muscle_group(
            diego.id, diego.household_id
        )

        assert "legs" not in last

    def test_the_other_member_of_the_house_does_not_leak_in(
        self, db: Session, diego: User, rocio: User
    ) -> None:
        """Regla 4 de `AGENTS.md`: el mismo hogar no alcanza para ver el dato del otro."""
        _workout(db, rocio, when=datetime.now(UTC), exercises=[("back", None)])

        assert (
            WorkoutRepository(db).get_last_trained_at_by_muscle_group(diego.id, diego.household_id)
            == {}
        )


class TestPerceivedEffort:
    def test_unrecorded_effort_is_absent_not_zero(self, db: Session, diego: User) -> None:
        """Un ejercicio sin RPE no es un ejercicio suave.

        Contarlo como cero hace que quien no completa el campo parezca no esforzarse, que
        es justo lo contrario de lo que el dato quiere decir.
        """
        _workout(
            db,
            diego,
            when=datetime.now(UTC) - timedelta(days=1),
            exercises=[("chest", 8), ("triceps", None), ("core", 6)],
        )

        efforts = WorkoutRepository(db).get_recent_perceived_effort(diego.id, diego.household_id)

        assert sorted(efforts) == [6, 8]

    def test_the_window_cuts_by_session_time(self, db: Session, diego: User) -> None:
        now = datetime.now(UTC)
        _workout(db, diego, when=now - timedelta(days=2), exercises=[("chest", 7)])
        _workout(db, diego, when=now - timedelta(days=40), exercises=[("chest", 3)])

        efforts = WorkoutRepository(db).get_recent_perceived_effort(
            diego.id, diego.household_id, days=14
        )

        assert efforts == [7]


class TestFoodCounts:
    def test_the_window_is_when_it_was_eaten_not_when_it_was_typed(
        self, db: Session, diego: User
    ) -> None:
        """La cena de la semana pasada anotada hoy es vieja para la comida y nueva para el id.

        La versión que esto reemplaza ordenaba por `MealItemConsumed.id`, así que cargar
        una comida atrasada la contaba como reciente.
        """
        now = datetime.now(UTC)
        _meal(db, diego, when=now - timedelta(days=1), items=[("cafe", None, None, None)])
        # Insertada después —id más alto— pero comida hace un mes.
        _meal(db, diego, when=now - timedelta(days=30), items=[("milanesa", None, None, None)])

        counts = MealRepository(db).get_food_counts_since(diego.id, now - timedelta(days=7))

        assert counts == {"cafe": 1}

    def test_names_are_grouped_case_insensitively(self, db: Session, diego: User) -> None:
        now = datetime.now(UTC)
        _meal(
            db,
            diego,
            when=now - timedelta(hours=2),
            items=[("Cafe", None, None, None), ("cafe", None, None, None)],
        )

        counts = MealRepository(db).get_food_counts_since(diego.id, now - timedelta(days=7))

        assert counts == {"cafe": 2}


class TestConsumedItems:
    def test_items_that_never_resolved_against_the_catalog_come_through(
        self, db: Session, diego: User, banana: FoodItem
    ) -> None:
        """Esconderlos acá haría que un total parcial se viera como un total.

        Que no se puedan sumar es una decisión de quien suma —y la registra en
        `MacroTotals.coverage`—, no un motivo para que el repositorio los oculte.
        """
        now = datetime.now(UTC)
        _meal(
            db,
            diego,
            when=now - timedelta(hours=1),
            items=[("banana", banana, 100.0, "g"), ("milanesa casera", None, 1.0, "unit")],
        )

        rows = MealRepository(db).get_consumed_items_since(diego.id, now - timedelta(days=14))

        assert {item.normalized_free_text_name for _, item in rows} == {
            "banana",
            "milanesa casera",
        }
        assert [item.food_item is None for _, item in rows].count(True) == 1


class TestRecentMetrics:
    def test_rows_are_not_filtered_by_any_single_column(self, db: Session, diego: User) -> None:
        """El contexto necesita las tres columnas opcionales de la misma fila.

        `get_weight_series` filtra `weight_kg IS NOT NULL` porque dibuja una línea; acá
        filtrar por una tira las filas donde la persona anotó otra, y quien anota sueño
        sin pesarse desaparecería de la lectura de sueño.
        """
        now = datetime.now(UTC)
        db.add_all(
            [
                BodyMetricLog(user_id=diego.id, timestamp=now - timedelta(days=2), weight_kg=70.0),
                BodyMetricLog(user_id=diego.id, timestamp=now - timedelta(days=1), sleep_hours=7.5),
            ]
        )
        db.flush()

        metrics = BodyMetricRepository(db).get_recent_metrics(diego.id, days=30)

        assert len(metrics) == 2
        # Del más viejo al más nuevo: una tendencia se lee en ese orden.
        assert metrics[0].weight_kg is not None and metrics[1].weight_kg is None


class TestLatestAnalyzedPanel:
    @staticmethod
    def _panel(
        db: Session, user: User, *, status: str, values: dict | None, when: date | None
    ) -> BloodAnalysis:
        row = BloodAnalysis(
            user_id=user.id,
            analysis_date=when,
            status=status,
            values_json=values,
            file_name="lab.pdf",
        )
        db.add(row)
        db.flush()
        return row

    def test_error_rows_and_empty_blobs_are_skipped(self, db: Session, diego: User) -> None:
        """Una subida sin marcadores no es un panel, y no puede tapar al que sí los tiene.

        Los tres casos son reales y solo el primero lo atajaba el `status`: la subida que
        falló queda en `error`, y las otras dos quedan en `analyzed` porque
        `analyze_file` devuelve su `values` con `default_factory=dict` — un archivo que
        el parser recorrió sin encontrar nada se guarda igual, con el blob vacío.
        """
        good = self._panel(
            db,
            diego,
            status="analyzed",
            values={"hemoglobin": {"value": 14.0, "status": "normal"}},
            when=date(2026, 1, 10),
        )
        self._panel(db, diego, status="error", values=None, when=date(2026, 6, 1))
        self._panel(db, diego, status="analyzed", values={}, when=date(2026, 7, 1))
        self._panel(db, diego, status="analyzed", values=None, when=date(2026, 8, 1))

        assert BloodAnalysisRepository(db).get_latest_analyzed(diego.id) is good

    def test_the_newest_analysis_date_wins(self, db: Session, diego: User) -> None:
        values = {"ldl": {"value": 180, "status": "high"}}
        self._panel(db, diego, status="analyzed", values=values, when=date(2023, 5, 5))
        recent = self._panel(db, diego, status="analyzed", values=values, when=date(2026, 5, 5))

        assert BloodAnalysisRepository(db).get_latest_analyzed(diego.id) is recent

    def test_someone_elses_panel_is_indistinguishable_from_a_missing_one(
        self, db: Session, diego: User, rocio: User
    ) -> None:
        """`get_owned` lleva el `user_id` en el `WHERE`, así que no hay 403 que filtrar.

        Es dato de salud del otro integrante: la respuesta correcta a "¿existe?" es la
        misma que a "no existe".
        """
        theirs = self._panel(
            db, rocio, status="analyzed", values={"tsh": {"value": 5.0}}, when=date(2026, 2, 2)
        )

        repo = BloodAnalysisRepository(db)
        assert repo.get_owned(theirs.id, diego.id) is None
        assert repo.get_latest_analyzed(diego.id) is None


class TestSuggestionWindows:
    @staticmethod
    def _card(
        db: Session,
        user: User | None,
        household: Household,
        *,
        subject: tuple[str, str],
        status: str = "pending",
        created_at: datetime | None = None,
        snoozed_until: datetime | None = None,
    ) -> Suggestion:
        card = Suggestion(
            scope_type="user" if user is not None else "household",
            scope_user_id=user.id if user is not None else None,
            household_id=household.id,
            category="meal",
            subject_type=subject[0],
            subject_name=subject[1],
            title=f"Probá {subject[1]}",
            text="…",
            rationale="…",
            source_type="rule",
            status=status,
            snoozed_until=snoozed_until,
        )
        if created_at is not None:
            card.created_at = created_at
        db.add(card)
        db.flush()
        return card

    def test_get_created_since_is_personal_not_household(
        self, db: Session, diego: User, rocio: User, household: Household
    ) -> None:
        """Una tarjeta del hogar no se le ofreció a nadie en particular.

        Contarla como oferta reciente de las dos personas haría que la compra que aceptó
        una le baje el score a la otra.
        """
        now = datetime.now(UTC)
        mine = self._card(db, diego, household, subject=("food", "avena"), created_at=now)
        self._card(db, rocio, household, subject=("food", "arroz"), created_at=now)
        self._card(db, None, household, subject=("food", "leche"), created_at=now)

        recent = SuggestionRepository(db).get_created_since(diego.id, now - timedelta(days=7))

        assert [c.id for c in recent] == [mine.id]

    def test_get_created_since_respects_the_cutoff(
        self, db: Session, diego: User, household: Household
    ) -> None:
        now = datetime.now(UTC)
        fresh = self._card(
            db, diego, household, subject=("food", "avena"), created_at=now - timedelta(days=1)
        )
        self._card(
            db, diego, household, subject=("food", "arroz"), created_at=now - timedelta(days=20)
        )

        recent = SuggestionRepository(db).get_created_since(diego.id, now - timedelta(days=7))

        assert [c.id for c in recent] == [fresh.id]

    def test_pending_and_snoozed_subjects_are_suppressed_answered_ones_are_not(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """Los dos casos de la 4.4.7, y el que no es: aceptar libera el sujeto."""
        now = datetime.now(UTC)
        self._card(db, diego, household, subject=("food", "avena"))  # pending
        self._card(
            db,
            diego,
            household,
            subject=("food", "arroz"),
            status="snoozed",
            snoozed_until=now + timedelta(days=2),
        )
        self._card(
            db,
            diego,
            household,
            subject=("food", "leche"),
            status="snoozed",
            snoozed_until=now - timedelta(days=2),
        )
        self._card(db, diego, household, subject=("food", "pan"), status="accepted")

        suppressed = SuggestionRepository(db).get_suppressed_subjects_for_user(diego.id)

        assert {name for _, name in suppressed} == {"avena", "arroz"}

    def test_the_household_query_needs_the_scope_not_just_the_house(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """Las personales también llevan `household_id`, así que filtrar por hogar no basta.

        Sin el `scope_type`, la tarjeta pendiente de una persona bloquearía el sujeto para
        las compras de la casa.
        """
        self._card(db, diego, household, subject=("food", "avena"))
        self._card(db, None, household, subject=("food", "leche"))

        suppressed = SuggestionRepository(db).get_suppressed_subjects_for_household(household.id)

        assert {name for _, name in suppressed} == {"leche"}


class TestInStock:
    def test_rows_at_zero_are_not_stock(
        self, db: Session, household: Household, banana: FoodItem
    ) -> None:
        """La pantalla de despensa muestra los ceros para reponerlos; cocinar no.

        `get_household_stock` los trae a propósito, y por eso no servía para "¿con qué se
        puede cocinar hoy?".
        """
        empty_food = FoodItem(canonical_name="arroz", base_unit="g", category="grain")
        db.add(empty_food)
        db.flush()
        db.add_all(
            [
                PantryStock(
                    household_id=household.id,
                    food_item_id=banana.id,
                    current_quantity=3,
                    unit="unit",
                ),
                PantryStock(
                    household_id=household.id,
                    food_item_id=empty_food.id,
                    current_quantity=0,
                    unit="g",
                ),
            ]
        )
        db.flush()

        rows = PantryStockRepository(db).get_in_stock(household.id)

        assert [r.food_item.canonical_name for r in rows] == ["banana"]


class TestMacroDayBoundary:
    def test_a_late_dinner_belongs_to_the_local_day_it_was_eaten(self) -> None:
        """22:00 de acá es el día siguiente en UTC, y los macros son del día de la casa.

        Se llama a `_macro_totals` directamente y con ítems armados a mano: lo que está
        bajo prueba es el criterio de día, no las consultas que lo alimentan.
        """
        tz = ZoneInfo(get_settings().timezone)
        day = date(2026, 3, 15)
        offset = datetime(2026, 3, 15, 12, tzinfo=tz).utcoffset() or timedelta(0)
        if offset < timedelta(0):
            local_meal = datetime(2026, 3, 15, 22, 0, tzinfo=tz)  # 01:00 UTC del 16
        elif offset > timedelta(0):
            local_meal = datetime(2026, 3, 15, 1, 0, tzinfo=tz)  # 22:00 UTC del 14
        else:
            pytest.skip("con la app en UTC no hay borde de día que cruzar")
        assert local_meal.astimezone(UTC).date() != day

        item = MealItemConsumed(normalized_free_text_name="milanesa", quantity=None, unit=None)
        today, baseline = _macro_totals([(local_meal, item)], day)

        assert today.items_total == 1, "la comida se contó en el día UTC, no en el local"
        assert baseline.items_total == 0

    def test_the_baseline_averages_recorded_days_not_calendar_days(
        self, db: Session, diego: User, banana: FoodItem
    ) -> None:
        """Dividir por 14 a quien anota tres veces por semana inventa un día de ayuno.

        Con la base cuatro veces más baja que su día real, cualquier día normal parece un
        exceso.
        """
        tz = ZoneInfo(get_settings().timezone)
        items = [
            (
                datetime(2026, 3, 10, 13, 0, tzinfo=tz),
                MealItemConsumed(
                    normalized_free_text_name="banana", quantity=100.0, unit="g", food_item=banana
                ),
            ),
            (
                datetime(2026, 3, 12, 13, 0, tzinfo=tz),
                MealItemConsumed(
                    normalized_free_text_name="banana", quantity=200.0, unit="g", food_item=banana
                ),
            ),
        ]

        _, baseline = _macro_totals(items, date(2026, 3, 15))

        # 89 kcal/100 g: un día de 89 y otro de 178 promedian 133.5, no 19 (267/14).
        assert baseline.calories == pytest.approx(133.5, abs=0.1)
        assert baseline.items_counted == 2


class TestBuildUserContext:
    def test_an_empty_account_yields_absences_not_zeros(self, db: Session, diego: User) -> None:
        """Una cuenta nueva no tiene nada, y el contexto que sale de ahí es válido.

        Quien lo lea tiene que tratar la ausencia como ausencia: `None` no es cero, y una
        tupla vacía no es "durmió 0 horas".
        """
        context = build_user_context(db, diego)

        assert context.user_id == diego.id
        assert context.weight is None
        assert context.body_fat is None
        assert context.sleep_hours == ()
        assert context.days_since_last_workout is None
        assert context.days_since_muscle_group == {}
        assert context.recent_effort == ()
        assert context.recent_food_counts == {}
        assert context.blood_panel is None
        assert context.average_sleep_hours is None
        assert context.average_effort is None
        assert context.days_since_training("legs") is None
        assert context.macros_today == MacroTotals()
        assert context.macros_today.coverage == 0.0

    def test_it_reads_the_data_the_engine_used_to_ignore(
        self, db: Session, diego: User, banana: FoodItem
    ) -> None:
        """Sueño, RPE, macros, recencia por grupo y panel: todo estaba en la base.

        Es el punto entero de 4.5.1 en un test: nada de esto es dato nuevo, solamente
        dato que nadie leía.
        """
        now = datetime.now(UTC)
        db.add_all(
            [
                BodyMetricLog(
                    user_id=diego.id,
                    timestamp=now - timedelta(days=5),
                    weight_kg=72.0,
                    body_fat_pct=20.0,
                ),
                BodyMetricLog(
                    user_id=diego.id,
                    timestamp=now - timedelta(days=1),
                    weight_kg=71.4,
                    body_fat_pct=19.5,
                    sleep_hours=6.0,
                ),
            ]
        )
        _workout(db, diego, when=now - timedelta(days=2), exercises=[("chest", 8)])
        _meal(db, diego, when=_local_noon_today(), items=[("banana", banana, 150.0, "g")])
        db.add(
            BloodAnalysis(
                user_id=diego.id,
                analysis_date=date(2026, 1, 1),
                status="analyzed",
                values_json={"ferritin": {"value": 12, "status": "low"}},
            )
        )
        db.flush()

        context = build_user_context(db, diego)

        assert context.weight is not None and context.weight.delta == -0.6
        assert context.body_fat is not None and context.body_fat.points == 2
        assert context.sleep_hours == (6.0,)
        assert context.average_sleep_hours == 6.0
        assert context.days_since_last_workout == 2
        assert context.days_since_training("chest") == 2
        assert context.recent_effort == (8,)
        assert context.recent_food_counts == {"banana": 1}
        # 89 kcal/100 g × 150 g
        assert context.macros_today.calories == pytest.approx(133.5, abs=0.1)
        assert context.macros_today.coverage == 1.0
        assert context.blood_panel is not None
        assert context.blood_panel.analysis_date == date(2026, 1, 1)
        assert context.blood_panel.age_days is not None

    def test_unconvertible_quantities_lower_the_coverage_instead_of_the_total(
        self, db: Session, diego: User, banana: FoodItem
    ) -> None:
        """ "2 unidades" necesitaría `serving_size_g`, que ninguna parte de la app escribe.

        Convertirla sería elegir un número, así que el ítem no se suma — y el total dice
        sobre cuántos ítems se calculó para que nadie lo lea como la proteína del día.
        """
        _meal(
            db,
            diego,
            when=_local_noon_today(),
            items=[("banana", banana, 100.0, "g"), ("banana", banana, 2.0, "unit")],
        )

        macros = build_user_context(db, diego).macros_today

        assert macros.items_total == 2
        assert macros.items_counted == 1
        assert macros.coverage == 0.5

    def test_the_exercise_catalog_can_be_empty(self, db: Session, diego: User) -> None:
        """Ningún fixture siembra `ExerciseType`, y eso es parte del contrato.

        Quien lo use tiene que funcionar con cero filas en vez de reponer una lista fija
        —que es exactamente lo que hace hoy `activity_generator._DEFAULT_ACTIVITIES` y lo
        que 4.5.2 va a cambiar.
        """
        assert build_user_context(db, diego).exercise_catalog == ()
