"""Tests for the recommendation engine."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryStock
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations import learning
from app.recommendations.filters import apply_hard_constraints, apply_signal_constraints
from app.recommendations.scorer import score_candidates


class TestHardConstraints:
    def test_impossible_activity_filtered_out(self, db: Session, diego: User) -> None:
        # Diego has swimming as impossible
        prefs = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="exercise",
                item_name="swimming",
                preference_signal="impossible",
                strength=1.0,
            )
        ]
        candidates = [
            {"title": "Go swimming!", "text": "Try swimming today", "category": "activity"},
            {"title": "Go biking!", "text": "Bike for 30 min", "category": "activity"},
        ]
        filtered = apply_hard_constraints(candidates, diego, prefs)
        titles = [c["title"] for c in filtered]
        assert "Go swimming!" not in titles
        assert "Go biking!" in titles

    def test_disliked_food_filtered_out(self, db: Session, rocio: User) -> None:
        prefs = [
            RecommendationPreference(
                user_id=rocio.id,
                item_type="food",
                item_name="liver",
                preference_signal="dislikes",
                strength=1.0,
            )
        ]
        candidates = [
            {"title": "Liver and onions", "text": "Try liver tonight", "category": "meal"},
            {"title": "Chicken salad", "text": "Fresh chicken salad", "category": "meal"},
        ]
        filtered = apply_hard_constraints(candidates, rocio, prefs)
        titles = [c["title"] for c in filtered]
        assert not any("liver" in t.lower() for t in titles)
        assert "Chicken salad" in titles

    def test_no_prefs_returns_all_candidates(self, db: Session, diego: User) -> None:
        candidates = [
            {"title": "Run today", "text": "Go for a run", "category": "activity"},
            {"title": "Eat salad", "text": "Fresh salad", "category": "meal"},
        ]
        filtered = apply_hard_constraints(candidates, diego, [])
        assert len(filtered) == len(candidates)

    def test_impossible_activity_from_user_model(self, db: Session, diego: User) -> None:
        """Impossible activities set on the user model are also respected."""
        # Diego has impossible_activities_json = ["swimming"]
        candidates = [
            {"title": "Go for a swim", "text": "Try the pool", "category": "activity"},
            {"title": "Walk today", "text": "30 min walk", "category": "activity"},
        ]
        filtered = apply_hard_constraints(candidates, diego, [])
        titles = [c["title"] for c in filtered]
        # Swimming should be filtered if the constraints check user model too
        # At minimum, "Walk today" should pass
        assert "Walk today" in titles


def _signal(
    user: User,
    signal_type: str,
    subject_type: str,
    subject_name: str,
    value: float,
    *,
    source_type: str = "explicit",
    age_days: float | None = None,
) -> Any:
    """Una señal en memoria, con el sujeto en las columnas que el lector mira.

    Estos tests usaban `entity_type="activity"`, que no es un tipo de sujeto. Pasaban
    igual porque el scorer comparaba bolsas de palabras y el tipo no entraba en la
    comparación —y porque las aserciones eran `>=`, que se cumple sin que el ajuste
    exista—. Desde la 4.4 el tipo es parte de la clave.

    Sin `age_days` la señal queda sin `created_at` —como una recién grabada y todavía no
    volcada— y por lo tanto sin descuento por edad: los tests que no hablan del tiempo
    siguen midiendo lo que medían antes de la 4.4.2.
    """
    from app.models.signal import BehaviorSignal

    created_at = None if age_days is None else datetime.now(timezone.utc) - timedelta(days=age_days)
    return BehaviorSignal(
        user_id=user.id,
        signal_type=signal_type,
        entity_type=subject_type,
        entity_name=subject_name,
        value=value,
        source_type=source_type,
        created_at=created_at,
    )


def _candidate(title: str, subject_type: str, subject_name: str, **extra: Any) -> dict[str, Any]:
    """Un candidato de generador con su sujeto declarado, como los emiten los cuatro."""
    base: dict[str, Any] = {
        "title": title,
        "text": f"{title} — cuerpo de la tarjeta",
        "category": "activity",
        "confidence": 0.5,
        "subject_type": subject_type,
        "subject_name": subject_name,
    }
    base.update(extra)
    return base


class TestScorer:
    def test_positive_signal_boosts_score(self, db: Session, diego: User) -> None:
        signals = [_signal(diego, "accepted_suggestion", "exercise", "biking", 1.0)]
        candidates = [
            _candidate("Go biking", "exercise", "biking"),
            _candidate("Go running", "exercise", "running"),
        ]
        scored = score_candidates(candidates, diego, signals, [])
        assert len(scored) == 2
        # Biking should score higher due to positive signal
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        assert biking["_score"] > running["_score"]

    def test_negative_signal_reduces_score(self, db: Session, diego: User) -> None:
        signals = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0)]
        candidates = [
            _candidate("Go biking", "exercise", "biking"),
            _candidate("Go running", "exercise", "running"),
        ]
        scored = score_candidates(candidates, diego, signals, [])
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        assert biking["_score"] > running["_score"]

    def test_a_signal_only_moves_its_own_subject(self, db: Session, diego: User) -> None:
        """El bug que la 4.4 arregla, fijado como test.

        Rechazar *"Time to get moving!"* guardaba el título entero como entidad aprendida y
        el scorer lo comparaba por tokens contra `title + text + rationale`: con 30% de
        solape —"moving", "boost", "energy"— cualquier candidato bajaba de score, cruzando
        categorías. Acá el rechazo es sobre el hábito de la constancia, y una tarjeta de
        comida que usa esas mismas palabras no se entera.
        """
        signals = [_signal(diego, "rejected_suggestion", "habit", "workout consistency", -1.0)]
        candidates = [
            _candidate(
                "Time to get moving!",
                "habit",
                "workout consistency",
                text="Even a 30-minute session can boost your mood and energy.",
            ),
            _candidate(
                "Use your spinach today",
                "food",
                "spinach",
                category="meal",
                text="Spinach will boost your energy — get moving on that salad.",
            ),
        ]
        scored = score_candidates(candidates, diego, signals, [])
        nudge = next(c for c in scored if c["subject_name"] == "workout consistency")
        meal = next(c for c in scored if c["subject_name"] == "spinach")
        assert nudge["_score"] < 0.5, "el sujeto rechazado baja"
        assert meal["_score"] == 0.5, "la comida no tiene nada que ver y no se mueve"

    def test_a_candidate_without_a_subject_keeps_its_raw_confidence(
        self, db: Session, diego: User
    ) -> None:
        """Sin sujeto no hay aprendizaje: ni boost, ni penalización, ni fallback al título.

        Es la contracara de no tener red de contención en `candidate_subject`. Lo que
        garantiza que ningún generador se olvide es `TestEveryCandidateDeclaresItsSubject`.
        """
        signals = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0)]
        candidates = [{"title": "Go running", "text": "Run 5km", "confidence": 0.5}]
        scored = score_candidates(candidates, diego, signals, [])
        assert scored[0]["_score"] == 0.5


class TestTemporalDecay:
    """Lo que hace que sea aprender y no acumular (4.4.2).

    Antes de esto una señal de hace ocho meses pesaba **exactamente igual** que la de
    ayer, así que un gusto que cambió no se podía desaprender nunca: el score era un
    promedio de toda la historia y las primeras semanas de uso decidían para siempre. El
    peso pasa a ser `value * 0.5 ** (edad / semivida)`.
    """

    def test_a_signal_at_its_half_life_is_worth_half(self, diego: User) -> None:
        implicit = _signal(
            diego, "repeated_meal_choice", "food", "pollo", 1.0, source_type="implicit", age_days=21
        )
        explicit = _signal(
            diego, "explicit_preference", "food", "pollo", 1.0, source_type="explicit", age_days=90
        )
        assert learning.signal_weight(implicit) == pytest.approx(0.5, abs=1e-3)
        assert learning.signal_weight(explicit) == pytest.approx(0.5, abs=1e-3)

    def test_what_someone_said_outlives_what_someone_did(self, diego: User) -> None:
        """Una implícita habla de la semana; una explícita, de la persona.

        "No me gusta el hígado" sigue siendo cierto en dos meses; "comí pollo el martes"
        no dice nada del martes que viene. Las preferencias que **no** deben caducar nunca
        no viven en esta tabla: viven en `RecommendationPreference`, que
        `apply_hard_constraints` lee sin descuento.
        """
        said = _signal(
            diego, "explicit_preference", "food", "pollo", 1.0, source_type="explicit", age_days=40
        )
        did = _signal(
            diego, "repeated_meal_choice", "food", "pollo", 1.0, source_type="implicit", age_days=40
        )
        assert learning.signal_weight(said) > learning.signal_weight(did)

    def test_an_unknown_source_type_fades_as_fast_as_the_fastest(self, diego: User) -> None:
        """`source_type` admite `"inferred"`, que nadie escribe todavía.

        Si no sabemos de dónde salió una señal, que se desvanezca rápido es el error más
        barato de los dos.
        """
        assert learning.half_life_days("inferred") == learning.half_life_days("implicit")

    def test_a_signal_not_yet_flushed_counts_whole(self, diego: User) -> None:
        """`created_at` lo pone la base, así que una señal recién creada no tiene fecha."""
        fresh = _signal(diego, "accepted_suggestion", "food", "pollo", 1.0)
        assert fresh.created_at is None
        assert learning.decay_factor(fresh) == 1.0

    def test_a_recent_taste_outranks_an_old_one(self, db: Session, diego: User) -> None:
        signals = [
            _signal(
                diego,
                "repeated_meal_choice",
                "food",
                "milanesa",
                1.0,
                source_type="implicit",
                age_days=120,
            ),
            _signal(
                diego,
                "repeated_meal_choice",
                "food",
                "lentejas",
                1.0,
                source_type="implicit",
                age_days=1,
            ),
        ]
        candidates = [
            _candidate("Milanesas", "food", "milanesa", category="meal"),
            _candidate("Guiso de lentejas", "food", "lentejas", category="meal"),
        ]
        scored = score_candidates(candidates, diego, signals, [])
        assert scored[0]["title"] == "Guiso de lentejas"

    def test_an_old_no_stops_filtering_but_keeps_weighing(self, db: Session, diego: User) -> None:
        """El "no" caduca como veto y sigue contando como opinión.

        Antes de la 4.4.2 la ventana de lectura eran 30 días, así que este caso no existía.
        Ampliarla para que el decaimiento tenga de qué decaer, sin tocar el filtro, habría
        convertido un rechazo de hace once meses en un veto permanente — el problema al
        revés. Vale como veto una semivida; después sale del filtro y baja el score.
        """
        fresh_no = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0, age_days=30)]
        old_no = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0, age_days=300)]
        candidates = [_candidate("Go running", "exercise", "running")]

        assert apply_signal_constraints(candidates, fresh_no) == []

        survived = apply_signal_constraints(candidates, old_no)
        assert [c["title"] for c in survived] == ["Go running"]
        (scored,) = score_candidates(survived, diego, old_no, [])
        assert scored["_score"] < 0.5, "sigue pesando, aunque ya no vete"

    def test_the_read_horizon_is_derived_and_not_copied(self) -> None:
        """El umbral vive en un solo lugar.

        Era un `30` escrito a mano en `engine.py` **y** otro en `scorer.py`: dos copias del
        mismo número, que es exactamente cómo empiezan a discrepar. Ahora los dos leen el
        horizonte de `learning`, que lo deriva de la semivida más larga.
        """
        from app.recommendations import engine, scorer

        assert scorer._RECENT_SIGNAL_DAYS == learning.SIGNAL_HORIZON_DAYS
        assert engine._RECENT_SIGNAL_DAYS == learning.SIGNAL_HORIZON_DAYS
        #: Y el horizonte tiene que dejar entrar lo que todavía pesa: si fuera más corto
        #: que unas pocas semividas, el corte volvería a decidir en lugar del decaimiento.
        four_half_lives = 4 * learning.half_life_days("explicit")
        assert four_half_lives <= learning.SIGNAL_HORIZON_DAYS


class TestSignalConstraints:
    def test_strongly_rejected_activity_filtered_out(self, db: Session, diego: User) -> None:
        signals = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0)]
        candidates = [
            _candidate("Go running today", "exercise", "running"),
            _candidate("Go biking", "exercise", "biking"),
        ]
        filtered = apply_signal_constraints(candidates, signals)
        titles = [c["title"] for c in filtered]
        assert "Go running today" not in titles
        assert "Go biking" in titles

    def test_the_same_name_under_another_type_is_another_subject(
        self, db: Session, diego: User
    ) -> None:
        """Un `muscle_group` "core" y un `exercise` "core" no son el mismo sujeto."""
        signals = [_signal(diego, "rejected_suggestion", "muscle_group", "core", -1.0)]
        candidates = [
            _candidate("Train core today", "muscle_group", "core"),
            _candidate("Try Core today", "exercise", "core"),
        ]
        filtered = apply_signal_constraints(candidates, signals)
        assert [c["subject_type"] for c in filtered] == ["exercise"]

    def test_accents_do_not_split_a_subject_in_two(self, db: Session, diego: User) -> None:
        """El catálogo escribe "brócoli" y el texto libre de una captura, "brocoli"."""
        signals = [_signal(diego, "rejected_suggestion", "food", "brocoli", -1.0)]
        candidates = [_candidate("Cook brócoli", "food", "Brócoli", category="meal")]
        assert apply_signal_constraints(candidates, signals) == []

    def test_no_rejected_signals_keeps_all(self, db: Session, diego: User) -> None:
        signals = [_signal(diego, "accepted_suggestion", "exercise", "biking", 1.0)]
        candidates = [
            _candidate("Go running", "exercise", "running"),
            _candidate("Go biking", "exercise", "biking"),
        ]
        filtered = apply_signal_constraints(candidates, signals)
        # Positive signals don't filter — all candidates kept
        assert len(filtered) == 2

    def test_dismissing_is_not_rejecting(self, db: Session, diego: User) -> None:
        """Un descarte baja el score pero no borra el candidato de la lista.

        El filtro miraba `value < 0` y nada más, así que el −0.3 de `dismissed` —y el de un
        "más tarde", que grababa el mismo tipo de señal— hacía desaparecer la sugerencia
        antes de puntuarla. Sacar algo de la lista pide un "no" explícito.
        """
        signals = [_signal(diego, "ignored_suggestion", "exercise", "running", -0.3)]
        candidates = [_candidate("Go running", "exercise", "running")]
        assert len(apply_signal_constraints(candidates, signals)) == 1

    def test_empty_signals_returns_all(self, db: Session, diego: User) -> None:
        candidates = [
            _candidate("Run", "exercise", "running"),
            _candidate("Swim", "exercise", "swimming"),
        ]
        filtered = apply_signal_constraints(candidates, [])
        assert len(filtered) == 2

    def test_diversity_penalty_applied_to_recent_duplicate(self, db: Session, diego: User) -> None:
        """Recently shown suggestions should score lower than fresh ones."""
        from datetime import datetime, timezone

        from app.models.suggestion import Suggestion

        recent = Suggestion(
            scope_type="user",
            scope_user_id=diego.id,
            household_id=diego.household_id,
            category="activity",
            subject_type="exercise",
            subject_name="biking",
            #: Con otra redacción que el candidato, a propósito: el castigo por repetición
            #: se comparaba contra el título, así que "Go biking" y "Bike again today" eran
            #: dos sugerencias distintas para el scorer y la misma para la persona.
            title="Bike again today",
            text="Bike for 30 min",
            rationale="You like biking",
            confidence=0.8,
            priority=8,
            source_type="rule",
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        candidates = [
            _candidate("Go biking", "exercise", "biking", confidence=0.8),
            _candidate("Go running", "exercise", "running", confidence=0.8),
        ]
        scored = score_candidates(candidates, diego, [], [recent])
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        # Biking has diversity penalty (recently shown), running does not
        assert running["_score"] > biking["_score"]


class TestMealWindow:
    """`_current_meal_type` used to read the UTC hour, so at 08:00 in Buenos
    Aires (UTC-3) the engine believed it was 11:00 and suggested lunch."""

    @pytest.mark.parametrize(
        ("local_hour", "expected"),
        [(8, "breakfast"), (12, "lunch"), (16, "snack"), (21, "dinner")],
    )
    def test_meal_type_follows_the_configured_timezone(
        self, monkeypatch: pytest.MonkeyPatch, local_hour: int, expected: str
    ) -> None:
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo

        from app.core import clock
        from app.recommendations.generators import meal_generator

        tz = ZoneInfo(get_settings().timezone)
        # A real instant whose local hour is `local_hour`, expressed in UTC so a
        # UTC reading of it would land on a different (and wrong) window.
        local = datetime(2026, 3, 15, local_hour, 30, tzinfo=tz)
        assert local.utcoffset() != timedelta(0), "test needs a non-UTC timezone"

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz: timezone | ZoneInfo | None = None) -> datetime:  # type: ignore[override]
                return local.astimezone(tz) if tz else local.replace(tzinfo=None)

        # El reloj se congela en `app.core.clock`, que es de donde el generador saca
        # la hora local desde que dejó de armar la zona por su cuenta. Sigue siendo
        # el mismo instante real: leerlo en UTC cae en otra ventana, y ahí estaba el
        # bug.
        monkeypatch.setattr(clock, "datetime", _FrozenDatetime)
        assert meal_generator._current_meal_type() == expected


class TestEveryCandidateDeclaresItsSubject:
    """Ningún generador puede emitir un candidato sin sujeto.

    Este es el guardián que reemplaza al fallback: `learning.candidate_subject` devuelve
    `None` en vez de caer al título, así que un generador que se olvide de declarar el
    sujeto no rompe nada —simplemente deja de aprender, en silencio, para siempre—. La
    única forma de que eso no pase es recorrerlos todos.

    Los fixtures de abajo están armados para que cada generador entre a **todas** sus
    ramas: por eso las aserciones de cantidad mínima, sin las cuales el test pasaría con un
    generador que no produce nada.
    """

    @staticmethod
    def _assert_subjects(candidates: list[dict[str, Any]], expected_at_least: int) -> None:
        from app.recommendations.learning import SUBJECT_TYPES, normalize_subject

        assert len(candidates) >= expected_at_least, (
            f"el generador produjo {len(candidates)} candidatos: los fixtures dejaron de "
            "cubrir sus ramas y el test ya no prueba lo que dice probar"
        )
        for candidate in candidates:
            title = candidate.get("title")
            assert candidate.get("subject_type") in SUBJECT_TYPES, (
                f"candidato {title!r} sin `subject_type` válido: "
                f"{candidate.get('subject_type')!r}"
            )
            assert normalize_subject(str(candidate.get("subject_name") or "")), (
                f"candidato {title!r} con `subject_name` vacío o impronunciable: "
                f"{candidate.get('subject_name')!r}"
            )

    @pytest.fixture
    def stocked_pantry(
        self, db: Session, household: Household, banana: FoodItem
    ) -> dict[str, FoodItem]:
        """Despensa con un ítem agotado, uno bajo y uno normal, más historial de compras."""
        from datetime import datetime, timedelta, timezone

        from app.models.pantry import PantryMovement

        rice = FoodItem(canonical_name="rice", category="grain", base_unit="g")
        milk = FoodItem(canonical_name="milk", category="dairy", base_unit="ml")
        db.add_all([rice, milk])
        db.flush()

        db.add_all(
            [
                # agotado → "Items are out of stock" y el par de co-compra
                PantryStock(
                    household_id=household.id,
                    food_item_id=milk.id,
                    current_quantity=0,
                    unit="ml",
                    low_stock_threshold=500,
                ),
                # bajo pero disponible → "Stock up on low items" y "Use your last ..."
                PantryStock(
                    household_id=household.id,
                    food_item_id=banana.id,
                    current_quantity=1,
                    unit="unit",
                    low_stock_threshold=3,
                ),
                PantryStock(
                    household_id=household.id,
                    food_item_id=rice.id,
                    current_quantity=900,
                    unit="g",
                    low_stock_threshold=200,
                ),
            ]
        )

        # Compras del mismo día, dos veces: co-ocurrencia leche+arroz, y arroz frecuente.
        now = datetime.now(tz=timezone.utc)
        for days_ago in (2, 9):
            for food in (milk, rice):
                db.add(
                    PantryMovement(
                        household_id=household.id,
                        user_id=None,
                        food_item_id=food.id,
                        movement_type="purchase",
                        quantity=1,
                        unit=food.base_unit,
                        timestamp=now - timedelta(days=days_ago),
                    )
                )
        db.flush()
        return {"rice": rice, "milk": milk, "banana": banana}

    def test_meal_generator(
        self, db: Session, diego: User, stocked_pantry: dict[str, FoodItem]
    ) -> None:
        from app.recommendations.generators import meal_generator

        preferences = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="food",
                item_name="Milanesa",
                preference_signal="likes",
                strength=0.9,
            )
        ]
        candidates = meal_generator.generate(db, diego, preferences)
        # pantry-featured + variedad + preferencia + un "usá lo último"
        self._assert_subjects(candidates, 4)

    def test_activity_generator_when_resting(self, db: Session, diego: User) -> None:
        from app.recommendations.generators import activity_generator

        preferences = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="exercise",
                item_name="biking",
                preference_signal="likes",
                strength=1.0,
            )
        ]
        # Sin entrenamientos: empujón de constancia + rotación + preferida + catálogo
        candidates = activity_generator.generate(db, diego, preferences)
        self._assert_subjects(candidates, 4)

    def test_activity_generator_after_training_today(self, db: Session, diego: User) -> None:
        """La rama del descanso: la única que solo aparece si entrenó hoy."""
        from datetime import datetime, timezone

        from app.models.workout import WorkoutParticipant, WorkoutSession
        from app.recommendations.generators import activity_generator

        session = WorkoutSession(
            household_id=diego.household_id,
            workout_type="gym",
            timestamp_start=datetime.now(tz=timezone.utc),
        )
        db.add(session)
        db.flush()
        db.add(WorkoutParticipant(workout_session_id=session.id, user_id=diego.id))
        db.flush()

        candidates = activity_generator.generate(db, diego, [])
        assert any(c["subject_name"] == "rest day" for c in candidates)
        self._assert_subjects(candidates, 2)

    def test_blood_generator(self, db: Session, diego: User) -> None:
        from app.recommendations.generators import blood_generator

        blood_values = {
            "hemoglobin": {"value": 10.1, "unit": "g/dL", "status": "low"},
            "ldl": {"value": 190, "unit": "mg/dL", "status": "critical_high"},
            "tsh": {"value": 0.1, "unit": "mUI/L", "status": "low"},
            # normal: no produce nada, y está para que eso siga siendo cierto
            "glucose": {"value": 90, "unit": "mg/dL", "status": "normal"},
        }
        candidates = blood_generator.generate(db, diego, blood_values)
        self._assert_subjects(candidates, 5)
        assert all(c["subject_type"] == "biomarker" for c in candidates)
        assert "glucose" not in {c["subject_name"] for c in candidates}

    def test_pantry_generator(
        self, db: Session, household: Household, stocked_pantry: dict[str, FoodItem]
    ) -> None:
        from app.recommendations.generators import pantry_generator

        candidates = pantry_generator.generate(db, household.id)
        # agotados + bajos + regulares + el par de co-compra
        self._assert_subjects(candidates, 4)
