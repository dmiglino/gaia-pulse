"""El generador de actividad: ventanas de recuperación, rotación y catálogo.

Vive en su propio módulo y no en `test_recommendations.py` porque lo que la 4.5.2 cambió es
una **conducta** y no un contador de candidatos: cuál grupo se propone, por qué ese y no otro,
y qué pasa cuando el catálogo está vacío. Los tests de `test_recommendations.py` miden que cada
generador declare sujetos válidos; estos miden que la app elija bien.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.models.workout import (
    ExerciseType,
    WorkoutExercise,
    WorkoutParticipant,
    WorkoutSession,
)
from app.recommendations.context import UserContext, build_user_context
from app.recommendations.generators import activity_generator
from app.recommendations.learning import MUSCLE_GROUPS, normalize_muscle_group


def _context(**overrides: object) -> UserContext:
    """Un contexto mínimo armado a mano, sin base.

    El generador no toca la sesión —todo lo que lee lo trae el contexto—, así que la mayoría
    de estos tests no necesitan base y por lo tanto tampoco necesitan fixtures: se declara el
    historial que se quiere medir y se lee la decisión.
    """
    base: dict[str, object] = {
        "user_id": 1,
        "now": datetime.now(tz=timezone.utc),
        "today": datetime.now(tz=timezone.utc).date(),
    }
    base.update(overrides)
    return UserContext(**base)  # type: ignore[arg-type]


class _FakeUser:
    """Un usuario sin base: el generador solo le lee las tres listas y el id."""

    id = 1
    impossible_activities_json: list[str] = []
    disliked_activities_json: list[str] = []
    preferred_activities_json: list[str] = []


def _user() -> User:
    return _FakeUser()  # type: ignore[return-value]


def _rotation(candidates: list[dict[str, object]]) -> dict[str, object] | None:
    return next((c for c in candidates if c["subject_type"] == "muscle_group"), None)


def _catalog_cards(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    return [c for c in candidates if c["subject_type"] == "exercise" and c["confidence"] == 0.55]


class TestVocabularyAndWindowsAgree:
    """Los tres lugares que hablan de grupos musculares tienen que decir lo mismo.

    Es el test que reemplaza al `assert` al importar: si alguien agrega un grupo al vocabulario
    y se olvida de darle ventana, el generador le daría el default en silencio y nadie se
    enteraría hasta ver una recomendación rara.
    """

    def test_every_group_in_the_vocabulary_has_a_recovery_window(self) -> None:
        assert set(activity_generator._RECOVERY_DAYS) == set(MUSCLE_GROUPS)

    def test_the_rotation_order_covers_the_vocabulary_exactly_once(self) -> None:
        order = activity_generator._ROTATION_PRIORITY
        assert set(order) == set(MUSCLE_GROUPS)
        assert len(order) == len(set(order))

    def test_every_muscle_group_the_nlp_emits_is_in_the_vocabulary(self) -> None:
        """El mapa del NLP conforma al vocabulario, que es lo que la 4.5.2 unificó.

        Antes emitía `triceps` y `biceps` como grupos propios y el catálogo escribe los dos
        como `arms`, así que el mismo músculo quedaba en dos claves y ninguna veía el estímulo
        de la otra.
        """
        from app.nlp.rules import _EXERCISE_MAP

        emitted = {group for _, group in _EXERCISE_MAP.values() if group}
        assert emitted <= MUSCLE_GROUPS

    def test_the_seed_catalog_conforms_to_the_vocabulary(self) -> None:
        """Los grupos que siembra `seed.py` también, y sin tocar una fila.

        Se lee del archivo y no de la base porque el seed no corre en los tests. Es lo que
        justifica haber elegido la **unión** como vocabulario: bajar el catálogo a los ocho del
        NLP habría obligado a partir `arms` en tríceps y bíceps, una distinción que ningún dato
        de esta casa sostiene.
        """
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parent.parent / "seed.py"
        block = source.read_text(encoding="utf-8").split("exercises_data = ", 1)[1]
        rows = ast.literal_eval(block[: block.index("\n    ]") + len("\n    ]")])
        groups = {row[2] for row in rows}
        assert len(groups) > 1
        assert {normalize_muscle_group(g) for g in groups} <= MUSCLE_GROUPS


class TestRecoveryWindowsAreWindows:
    """Cada grupo se compara contra **su** ventana, no contra un 2 plano para todos."""

    def test_core_frees_up_the_next_day_and_legs_do_not(self) -> None:
        context = _context(days_since_muscle_group={"core": 1, "legs": 1})
        recovering = activity_generator._recovering_groups(context)
        assert recovering == {"legs"}

    def test_a_group_is_out_of_its_window_on_the_day_it_equals_it(self) -> None:
        """`< ventana` y no `<= ventana`, porque los días vienen truncados.

        Un estímulo de hace 2.5 días llega como `days_since == 2`, y contarlo dentro de una
        ventana de 2 lo trataría como más fresco de lo que es.
        """
        assert (
            activity_generator._recovering_groups(_context(days_since_muscle_group={"chest": 2}))
            == set()
        )
        assert activity_generator._recovering_groups(
            _context(days_since_muscle_group={"chest": 1})
        ) == {"chest"}

    def test_an_unknown_group_gets_the_documented_default_and_not_an_error(self) -> None:
        """La columna es texto libre: un grupo que nadie declaró tiene que tener conducta."""
        assert (
            activity_generator._recovery_window("forearms")
            == activity_generator._DEFAULT_RECOVERY_DAYS
        )
        recovering = activity_generator._recovering_groups(
            _context(days_since_muscle_group={"forearms": 1})
        )
        assert recovering == {"forearms"}


class TestRotationPicksTheMostOverdue:
    def test_it_picks_the_group_that_has_been_waiting_longest(self) -> None:
        """Y no el primero por orden alfabético, que era el bug de "casi siempre back"."""
        context = _context(
            days_since_muscle_group={group: 1 for group in MUSCLE_GROUPS} | {"chest": 30}
        )
        assert (
            _rotation(activity_generator.generate(_user(), [], context))["subject_name"] == "chest"
        )

    def test_it_measures_overdue_against_each_window_and_not_raw_days(self) -> None:
        """`core` con 5 días (ventana 1) está 4 días atrasado; `legs` con 6 (ventana 3), 3.

        En días crudos ganaría `legs`. Lo que decide es cuánto hace que pasó su ventana, que es
        el cambio de conducta de la 4.5.2.
        """
        context = _context(
            days_since_muscle_group={group: 1 for group in MUSCLE_GROUPS} | {"core": 5, "legs": 6}
        )
        assert (
            _rotation(activity_generator.generate(_user(), [], context))["subject_name"] == "core"
        )

    def test_ties_break_by_declared_priority_and_not_alphabetically(self) -> None:
        """Con todo empatado gana `legs`, que es el primero de `_ROTATION_PRIORITY`.

        Alfabéticamente ganaría `arms`, y antes de la 4.5.2 ganaba `back` por la misma razón:
        el orden era un accidente del `sorted()`, no una decisión.

        El empate se arma sumándole los mismos días a la ventana de cada grupo, y no dándoles
        el mismo `days_since`: con 10 días para todos no hay empate ninguno —`core` estaría 9
        días atrasado y `legs` 7—, que es justamente el punto del test anterior.
        """
        context = _context(
            days_since_muscle_group={
                group: window + 5 for group, window in activity_generator._RECOVERY_DAYS.items()
            }
        )
        assert (
            _rotation(activity_generator.generate(_user(), [], context))["subject_name"] == "legs"
        )

    def test_a_never_trained_group_wins_over_everything_overdue(self) -> None:
        context = _context(
            days_since_muscle_group={group: 90 for group in MUSCLE_GROUPS if group != "shoulders"}
        )
        card = _rotation(activity_generator.generate(_user(), [], context))
        assert card["subject_name"] == "shoulders"

    def test_never_trained_and_overdue_are_different_cards(self) -> None:
        """ "Nunca entrenaste esto" y "hace ocho días" son propuestas distintas.

        Una es empezar y la otra es volver, y el mapa del contexto distingue el caso a
        propósito: decirle "volvé" a alguien que nunca fue es cómo una app revela que no está
        mirando.
        """
        never = _rotation(activity_generator.generate(_user(), [], _context()))
        overdue = _rotation(
            activity_generator.generate(
                _user(),
                [],
                _context(days_since_muscle_group={group: 20 for group in MUSCLE_GROUPS}),
            )
        )
        assert "haven't logged" in never["text"]
        assert "20 days since" in overdue["text"]

    def test_no_rotation_card_when_every_group_is_still_recovering(self) -> None:
        """El día después de una sesión de cuerpo entero no hay nada honesto que proponer."""
        context = _context(
            days_since_last_workout=1,
            days_since_muscle_group={group: 0 for group in MUSCLE_GROUPS},
        )
        assert _rotation(activity_generator.generate(_user(), [], context)) is None

    def test_an_unknown_group_is_read_as_a_stimulus_but_never_proposed(self) -> None:
        """Las dos direcciones son distintas: leer no inventa, proponer es un conjunto cerrado."""
        context = _context(days_since_muscle_group={"forearms": 0})
        candidates = activity_generator.generate(_user(), [], context)
        card = _rotation(candidates)
        assert card is not None
        assert card["subject_name"] in MUSCLE_GROUPS


class TestAliasesCollapseIntoOneGroup:
    def test_triceps_and_arms_are_the_same_group(self) -> None:
        assert normalize_muscle_group("triceps") == "arms"
        assert normalize_muscle_group("Biceps") == "arms"
        assert normalize_muscle_group("full body") == "full_body"

    def test_an_unknown_group_keeps_its_own_name(self) -> None:
        """Y no se fusiona con `other`, que ya significa otra cosa (`muscle_group IS NULL`)."""
        assert normalize_muscle_group("Forearms") == "forearms"

    def test_a_triceps_stimulus_counts_as_arms_in_the_context(
        self, db: Session, diego: User
    ) -> None:
        """El NLP graba "triceps" en la columna y el generador pregunta por "arms".

        Sin la colapsada el mismo músculo quedaba en dos claves y el generador proponía brazos
        el día después de haberlos entrenado.
        """
        session = WorkoutSession(
            household_id=diego.household_id,
            workout_type="gym",
            timestamp_start=datetime.now(tz=timezone.utc),
        )
        db.add(session)
        db.flush()
        participant = WorkoutParticipant(workout_session_id=session.id, user_id=diego.id)
        db.add(participant)
        db.flush()
        db.add(
            WorkoutExercise(
                workout_session_id=session.id,
                workout_participant_id=participant.id,
                exercise_name="triceps",
                muscle_group="triceps",
            )
        )
        db.flush()

        context = build_user_context(db, diego)
        assert context.days_since_muscle_group.get("arms") == 0
        assert "triceps" not in context.days_since_muscle_group
        assert context.days_since_training("triceps") == 0

    def test_the_more_recent_of_two_aliases_wins(self, db: Session, diego: User) -> None:
        """`arms` de hoy y `biceps` de hace una semana son un grupo entrenado hoy."""
        now = datetime.now(tz=timezone.utc)
        for name, group, when in (
            ("curl", "biceps", now - timedelta(days=7)),
            ("pushdown", "arms", now),
        ):
            session = WorkoutSession(
                household_id=diego.household_id, workout_type="gym", timestamp_start=when
            )
            db.add(session)
            db.flush()
            participant = WorkoutParticipant(workout_session_id=session.id, user_id=diego.id)
            db.add(participant)
            db.flush()
            db.add(
                WorkoutExercise(
                    workout_session_id=session.id,
                    workout_participant_id=participant.id,
                    exercise_name=name,
                    muscle_group=group,
                )
            )
            db.flush()

        assert build_user_context(db, diego).days_since_muscle_group["arms"] == 0


@pytest.fixture
def catalog(db: Session) -> list[ExerciseType]:
    """Un catálogo mínimo, con las tres categorías del seed. **Opt-in a propósito.**

    No es `autouse` porque el catálogo vacío es un caso real —la base recién creada— y
    `test_user_context.py::test_the_exercise_catalog_can_be_empty` lo fija. Un fixture
    automático haría que ningún test volviera a medir ese camino.
    """
    rows = [
        ExerciseType(
            name="Bench Press",
            category="strength",
            muscle_group="chest",
            intensity="high",
        ),
        ExerciseType(
            name="Barbell Row", category="strength", muscle_group="back", intensity="high"
        ),
        ExerciseType(name="Plank", category="strength", muscle_group="core", intensity="moderate"),
        ExerciseType(name="Walking", category="cardio", muscle_group="full_body", intensity="low"),
        ExerciseType(
            name="Yoga", category="flexibility", muscle_group="full_body", intensity="low"
        ),
    ]
    db.add_all(rows)
    db.flush()
    return rows


class TestCatalogCards:
    def test_an_empty_catalog_produces_no_named_card_and_logs_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Borrar las ocho actividades sin reponerlas disfrazadas de fallback.

        Las tarjetas de descanso, constancia y rotación no necesitan nombre, así que el
        generador sigue teniendo algo que decir con el catálogo vacío.
        """
        with caplog.at_level("WARNING"):
            candidates = activity_generator.generate(_user(), [], _context())
        assert _catalog_cards(candidates) == []
        assert candidates
        assert "catalog is empty" in caplog.text

    def test_it_picks_at_most_one_card_per_category(self, catalog: list[ExerciseType]) -> None:
        """Dos ejercicios de fuerza son la misma propuesta con distinto nombre."""
        context = _context(exercise_catalog=tuple(catalog))
        rows = activity_generator._catalog_rows(
            context, recovering=set(), excluded=set(), days_since=None
        )
        assert len(rows) == activity_generator._MAX_CATALOG_CARDS
        assert len({row.category for row in rows}) == len(rows)

    def test_the_order_is_by_overdue_group_and_not_by_name(
        self, catalog: list[ExerciseType]
    ) -> None:
        """Alfabéticamente la primera fila del catálogo es siempre "Barbell Row".

        Ordenar por nombre —que es lo que devuelve `list_all()`— reproduciría el bug de la
        4.5.2 con otra cara: la misma tarjeta para siempre. Acá `chest` hace 30 días que espera
        y `back` uno, así que gana "Bench Press".
        """
        context = _context(
            days_since_last_workout=1,
            days_since_muscle_group={group: 20 for group in MUSCLE_GROUPS}
            | {"back": 1, "chest": 30},
            exercise_catalog=tuple(catalog),
        )
        rows = activity_generator._catalog_rows(
            context,
            recovering=activity_generator._recovering_groups(context),
            excluded=set(),
            days_since=None,
        )
        assert rows[0].name == "Bench Press"

    def test_it_skips_a_group_that_is_still_recovering(self, catalog: list[ExerciseType]) -> None:
        context = _context(
            days_since_muscle_group={"chest": 0, "back": 0, "core": 0, "full_body": 0},
            exercise_catalog=tuple(catalog),
        )
        rows = activity_generator._catalog_rows(
            context,
            recovering=activity_generator._recovering_groups(context),
            excluded=set(),
            days_since=0,
        )
        assert rows == []

    def test_it_skips_high_intensity_right_after_a_workout(
        self, catalog: list[ExerciseType]
    ) -> None:
        context = _context(days_since_last_workout=1, exercise_catalog=tuple(catalog))
        rows = activity_generator._catalog_rows(
            context,
            recovering=set(),
            excluded=set(),
            days_since=1,
        )
        assert all((row.intensity or "").lower() != "high" for row in rows)

    def test_it_skips_what_the_person_cannot_or_will_not_do(
        self, catalog: list[ExerciseType]
    ) -> None:
        context = _context(exercise_catalog=tuple(catalog))
        rows = activity_generator._catalog_rows(
            context,
            recovering=set(),
            excluded={"bench press", "walking"},
            days_since=None,
        )
        assert {row.name for row in rows}.isdisjoint({"Bench Press", "Walking"})

    def test_a_catalog_card_declares_the_exercise_as_its_subject(
        self, db: Session, diego: User, catalog: list[ExerciseType]
    ) -> None:
        """Y no el grupo, que ya es el sujeto de la tarjeta de rotación.

        Dos tarjetas con el mismo sujeto se estorban entre sí en el dedup y se aprenden como
        una sola cosa.
        """
        candidates = activity_generator.generate(diego, [], build_user_context(db, diego))
        cards = _catalog_cards(candidates)
        assert cards
        rotation = _rotation(candidates)
        assert rotation is not None
        assert all(c["subject_name"] != rotation["subject_name"] for c in cards)


class TestPreferencesStillWin:
    def test_a_preferred_activity_is_not_repeated_by_the_catalog(
        self, db: Session, diego: User, catalog: list[ExerciseType]
    ) -> None:
        preferences = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="exercise",
                item_name="Walking",
                preference_signal="likes",
                strength=1.0,
            )
        ]
        candidates = activity_generator.generate(diego, preferences, build_user_context(db, diego))
        names = [c["subject_name"] for c in candidates]
        assert names.count("walking") == 1
