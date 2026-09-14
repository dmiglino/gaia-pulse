"""Tests for the recommendation engine."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryMovement, PantryStock
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.models.workout import ExerciseType
from app.recommendations import learning, scorer
from app.recommendations.context import BloodPanel, build_user_context
from app.recommendations.filters import apply_hard_constraints, apply_signal_constraints
from app.recommendations.generators import blood_generator, pantry_generator
from app.recommendations.scorer import score_candidates


def _panel(values: dict[str, Any], age_days: int | None) -> BloodPanel:
    """Un panel de sangre con la antigüedad que el test quiere medir y la fecha que le toca.

    Los dos campos tienen que contar la misma historia: `age_days` es lo que el generador lee
    para decidir la banda, y `analysis_date` es lo que sale escrito en la tarjeta. Armarlos
    por separado en cada caso es cómo se cuela un panel que dice "hace ocho meses" con la
    fecha de ayer, que pasaría verde midiendo una contradicción. `age_days=None` es el panel
    cuya fecha el parser no pudo leer, y ahí no hay fecha que poner.
    """
    analysis_date = None if age_days is None else date.today() - timedelta(days=age_days)
    return BloodPanel(values=values, analysis_date=analysis_date, age_days=age_days)


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

    def test_an_unknown_category_is_checked_against_both_blocked_sets(
        self, db: Session, diego: User
    ) -> None:
        """El bypass de `category="habit"`: las tres tarjetas de sangre que lo tenían.

        `_sides_to_check` devolvía `None` para una categoría que no reconocía, y las dos ramas
        de descarte comparaban contra `"food"` y `"activity"`, así que un `None` no se
        comparaba contra **ninguno** de los dos conjuntos. El alcance real son las tarjetas de
        TSH y creatinina, que son justo las que llevan consejo de salud.
        """
        prefs = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="food",
                item_name="liver",
                preference_signal="avoid",
                strength=1.0,
            )
        ]
        candidates = [
            {
                "title": "Consider discussing thyroid function",
                "text": "Organ meats such as liver are one dietary route people take.",
                "category": "habit",
            },
            {
                "title": "Consider a follow-up",
                "text": "Nothing here names anything you blocked.",
                "category": "habit",
            },
        ]
        filtered = apply_hard_constraints(candidates, diego, prefs)
        titles = [c["title"] for c in filtered]
        assert "Consider discussing thyroid function" not in titles
        assert "Consider a follow-up" in titles

    def test_an_unknown_category_is_also_checked_against_activity_blocks(
        self, db: Session, diego: User
    ) -> None:
        """Los dos conjuntos, no solo el de comida: Diego tiene `swimming` como imposible."""
        candidates = [
            {
                "title": "A habit worth building",
                "text": "Some people take up swimming for this.",
                "category": "habit",
            }
        ]
        assert apply_hard_constraints(candidates, diego, []) == []

    def test_a_known_category_still_only_checks_its_own_side(
        self, db: Session, diego: User
    ) -> None:
        """La ampliación es para lo que no se pudo clasificar, no para todo.

        Si una tarjeta de comida se midiera también contra los bloqueos de actividad, el
        match por substring de `_any_token_matches` la borraría por accidente: Diego no puede
        nadar, y "swimming in olive oil" no es una propuesta de natación.
        """
        candidates = [
            {
                "title": "Bread swimming in olive oil",
                "text": "A simple dinner.",
                "category": "meal",
            }
        ]
        filtered = apply_hard_constraints(candidates, diego, [])
        assert [c["title"] for c in filtered] == ["Bread swimming in olive oil"]

    def test_with_no_blocks_at_all_every_candidate_survives(self, db: Session, rocio: User) -> None:
        """Lo que el atajo borrado garantizaba, ahora medido en vez de cortocircuitado.

        `rocio` no declara bloqueos, así que los dos conjuntos llegan vacíos: el recorrido
        completo tiene que dejar pasar todo, incluida una categoría desconocida que ahora
        mira los dos lados.
        """
        candidates: list[dict[str, Any]] = [
            {"title": "Eat salad", "text": "Fresh salad", "category": "meal"},
            {"title": "Run today", "text": "Go for a run", "category": "activity"},
            {"title": "Sleep earlier", "text": "Wind down before midnight", "category": "habit"},
        ]
        assert apply_hard_constraints(candidates, rocio, []) == candidates


def _signal(
    user: User,
    signal_type: str,
    subject_type: str,
    subject_name: str,
    value: float,
    *,
    source_type: str = "explicit",
    age_days: float | None = None,
    context: dict[str, Any] | None = None,
) -> Any:
    """Una señal en memoria, con el sujeto en las columnas que el lector mira.

    Estos tests usaban `entity_type="activity"`, que no es un tipo de sujeto. Pasaban
    igual porque el scorer comparaba bolsas de palabras y el tipo no entraba en la
    comparación —y porque las aserciones eran `>=`, que se cumple sin que el ajuste
    exista—. Desde la 4.4 el tipo es parte de la clave.

    Sin `age_days` la señal queda sin `created_at` —como una recién grabada y todavía no
    volcada— y por lo tanto sin descuento por edad: los tests que no hablan del tiempo
    siguen midiendo lo que medían antes de la 4.4.2.

    Con una excepción desde la 4.4.6: para la saciedad, una señal de consumo sin fecha es
    consumo que está pasando **ahora**, o sea presión máxima. No es un accidente del
    helper, es el caso correcto —un acto en curso llena—, pero significa que un test sobre
    el gusto que use señales de consumo tiene que darles una edad, o va a medir las dos
    cosas a la vez.
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
        context_json=context,
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
            #: Diez días y no uno: desde la 4.4.6 una comida de ayer también produce
            #: saciedad, y este test habla del decaimiento del gusto, no de eso. A diez
            #: días la saciedad ya se apagó y lo único que queda comparando es la edad.
            _signal(
                diego,
                "repeated_meal_choice",
                "food",
                "lentejas",
                1.0,
                source_type="implicit",
                age_days=10,
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


class TestConfidenceByEvidence:
    """Un toque no es una regla (4.4.3).

    Hasta acá el ajuste era `knob * min(|suma de pesos|, 1.0)`: el tope existía para que la
    suma no se desbordara —cada comida registrada escribe un `repeated_meal_choice` de
    1.0—, no para modelar cuánto sabe la app. La consecuencia era que un único tap movía el
    score exactamente igual que diez observaciones consistentes, y que un descarte
    accidental pesaba como una decisión.
    """

    def test_one_observation_moves_less_than_six(self, db: Session, diego: User) -> None:
        candidate = _candidate("Go biking", "exercise", "biking")
        #: Con fecha, y no de este instante: seis salidas en bici pasan en varias semanas,
        #: no en un segundo, y una señal sin fecha la lee la saciedad (4.4.6) como algo que
        #: está pasando ahora mismo. Este test mide cuánto sabe la app, no cuánto hubo hace
        #: un rato, así que las pone donde la vida las pone: en el pasado.
        once = [_signal(diego, "repeated_activity", "exercise", "biking", 1.0, age_days=14)]
        six_times = once * 6

        (weak,) = score_candidates([candidate], diego, once, [])
        (firm,) = score_candidates([candidate], diego, six_times, [])
        assert 0.5 < weak["_score"] < firm["_score"]

    def test_confidence_saturates_instead_of_growing(self, db: Session, diego: User) -> None:
        """La diferencia entre seis y veinte observaciones no debería mover el score.

        Es lo que separa "aprender que le gusta" de "contar cuántas veces lo comió": sin
        saturación un alimento de todos los días se lleva el ajuste entero por delante de
        todo lo demás, para siempre, y el loop empuja a repetir en vez de a variar.

        Se mide sobre la fuerza aprendida y no sobre el score, aunque el score es lo que
        se ve. La razón: el ajuste es `knob * fuerza`, o sea lineal, así que la forma de la
        curva es exactamente la misma en los dos lados — pero el score suma además el
        decaimiento y, desde la 4.4.6, la saciedad, y las tres cosas juntas no dejan medir
        ninguna. Poner una fecha para apagar la saciedad apaga también la mitad de la
        evidencia, y la curva medida así ya no es la de la saturación.
        """
        ate_it = _signal(
            diego, "repeated_meal_choice", "food", "milanesa", 1.0, source_type="implicit"
        )

        def strength_after(times: int) -> float:
            (learned,) = learning.subject_affinities([ate_it] * times).values()
            return learned.strength

        once, six, many = strength_after(1), strength_after(6), strength_after(24)
        #: La primera observación enseña; la vigésima ya no. Que el primer tramo mueva más
        #: que el cuarto —cuatro veces más señales— es la saturación misma.
        assert once < six < many
        assert (six - once) > 2 * (many - six)
        #: Y nunca más allá del knob: el ajuste está acotado por construcción, no por un
        #: `min()` puesto a mano en el scorer.
        assert scorer._learned_delta(many) <= scorer._POSITIVE_SIGNAL_BOOST

    def test_what_is_learned_is_an_average_and_not_a_tally(self, diego: User) -> None:
        """Diez veces sí y una vez no sigue siendo "sí", y con más certeza que una sola vez.

        Con la suma cruda, "10 sí + 1 no" y "9 sí" eran el mismo número y las dos cosas
        estaban recortadas al mismo tope: la app no podía distinguir "le gusta" de "le
        gusta y ya lo vi muchas veces".
        """
        ten_yes = [
            _signal(diego, "repeated_meal_choice", "food", "pollo", 1.0, source_type="implicit")
        ] * 10
        one_no = [_signal(diego, "rejected_suggestion", "food", "pollo", -1.0)]
        (learned,) = learning.subject_affinities(ten_yes + one_no).values()

        assert learned.evidence == pytest.approx(11.0)
        assert learned.direction == pytest.approx(9 / 11)
        assert learned.strength > 0

        (thin,) = learning.subject_affinities(ten_yes[:1]).values()
        assert thin.direction == pytest.approx(1.0), "una sola señal apunta derecho"
        assert thin.strength < learned.strength, "pero sabe mucho menos"

    def test_a_subject_with_no_signals_has_no_opinion(self, diego: User) -> None:
        empty = learning.SubjectAffinity(net=0.0, evidence=0.0)
        assert (empty.direction, empty.confidence, empty.strength) == (0.0, 0.0, 0.0)

    def test_a_single_tap_no_longer_swings_the_whole_penalty(
        self, db: Session, diego: User
    ) -> None:
        """El caso que motivó la 4.4.3: un descarte accidental.

        Sigue bajando el score —es información— pero ya no lo baja tanto como una decisión
        repetida. Lo que ese tap **sí** hace de entrada es sacar al sujeto de la lista por
        una semivida (`apply_signal_constraints`), que es la parte que la persona pidió
        explícitamente al apretar "no".
        """
        candidate = _candidate("Go running", "exercise", "running")
        one_no = [_signal(diego, "rejected_suggestion", "exercise", "running", -1.0)]
        four_nos = one_no * 4

        (after_one,) = score_candidates([candidate], diego, one_no, [])
        (after_four,) = score_candidates([candidate], diego, four_nos, [])
        assert after_four["_score"] < after_one["_score"] < 0.5
        assert after_one["_score"] > 0.5 - scorer._NEGATIVE_SIGNAL_PENALTY / 2


#: El índice de atributos tal como lo devolvería el catálogo sembrado, para los tests que
#: hablan del scorer y no de la consulta. Que sea un literal es a propósito: el scorer no
#: toca la base, y lo único que necesita saber es a qué categoría pertenece cada nombre.
_VEGETABLES = ("brocoli", "coliflor", "kale", "espinaca")
_FOOD_ATTRIBUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("food", name): ("food_category", "vegetable") for name in _VEGETABLES
} | {
    ("food", "milanesa"): ("food_category", "protein"),
    ("food", "banana"): ("food_category", "fruit"),
}

#: La otra mitad del índice desde la 4.5.8: el catálogo de ejercicios, por grupo muscular.
#: Los nombres son los del catálogo —normalizados, que es como los declara una tarjeta de
#: actividad— y **no** hay ninguna entrada `("muscle_group", …)`: que un grupo no sea clave de
#: su propio balde es la asimetría que `attribute_index` documenta.
_EXERCISE_ATTRIBUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("exercise", name): ("muscle_group", "chest")
    for name in ("bench press", "push ups", "incline press")
}


class TestAttributeLevelLearning:
    """Aprender la categoría, no solo el nombre exacto (4.4.4).

    Hasta acá lo aprendible era el sujeto puntual, así que rechazar brócoli, coliflor y
    kale no enseñaba **nada** sobre la espinaca: un alimento sin señales propias salía con
    su confianza cruda, aunque la app supiera de sobra qué opina de las verduras. Eso es
    recordar; esto es aprender.
    """

    def test_three_rejected_vegetables_move_an_unseen_one(self, diego: User) -> None:
        """El ejemplo del plan, palabra por palabra."""
        rejections = [
            _signal(diego, "rejected_suggestion", "food", name, -1.0)
            for name in ("brocoli", "coliflor", "kale")
        ]
        spinach = _candidate("Espinaca a la crema", "food", "espinaca", category="meal")

        (blind,) = score_candidates([spinach], diego, rejections, [])
        (taught,) = score_candidates(
            [spinach], diego, rejections, [], subject_attributes=_FOOD_ATTRIBUTES
        )
        assert blind["_score"] == 0.5, "sin el índice, una espinaca sin señales no se mueve"
        assert taught["_score"] < 0.5

    def test_a_generalization_stays_weaker_than_direct_evidence(self, diego: User) -> None:
        """Una categoría es una de las razones por las que algo gusta, nunca la razón entera.

        Sin `_ATTRIBUTE_SIGNAL_SCALE` esto no se sostendría: la confianza satura hacia 1, así
        que con suficientes verduras registradas —meses, no años— una verdura que la persona
        nunca comió llegaría al mismo ajuste que su comida favorita.
        """
        many_rejections = [
            _signal(diego, "rejected_suggestion", "food", name, -1.0) for name in _VEGETABLES[:3]
        ] * 30
        unseen = _candidate("Espinaca a la crema", "food", "espinaca", category="meal")
        rejected_itself = _candidate("Brócoli al vapor", "food", "brocoli", category="meal")

        by_category, direct = score_candidates(
            [unseen, rejected_itself],
            diego,
            many_rejections,
            [],
            subject_attributes=_FOOD_ATTRIBUTES,
        )
        assert by_category["subject_name"] == "espinaca"
        assert direct["_score"] < by_category["_score"] < 0.5

    def test_a_favourite_does_not_boost_itself_through_its_own_category(self, diego: User) -> None:
        """Descontarse es lo que hace que los dos niveles no digan lo mismo dos veces.

        Un alimento de todos los días es el que más aporta a su categoría: sin la resta de
        `SubjectAffinity.without` cobraría el ajuste puntual y otra vez, en chico, por su
        propia evidencia — un favorito con un ajuste más grande que el knob, por partida
        doble, sin que hubiera aparecido ni un dato nuevo.
        """
        ate_it = [
            _signal(diego, "repeated_meal_choice", "food", "milanesa", 1.0, source_type="implicit")
        ] * 12
        candidate = _candidate("Milanesas", "food", "milanesa", category="meal")

        (alone,) = score_candidates([candidate], diego, ate_it, [])
        (with_index,) = score_candidates(
            [candidate], diego, ate_it, [], subject_attributes=_FOOD_ATTRIBUTES
        )
        assert with_index["_score"] == alone["_score"]
        assert alone["_score"] <= 0.5 + scorer._POSITIVE_SIGNAL_BOOST

    def test_the_attribute_needs_more_evidence_than_the_subject(self, diego: User) -> None:
        """La vara más alta, medida: una verdura rechazada mueve mucho menos que el sujeto.

        Y tres mueven más del doble que una, que es lo que distingue un patrón de una
        coincidencia: si una sola señal ya generalizara casi igual que tres, cualquier
        semana rara reescribiría una categoría entera —y una categoría son treinta
        alimentos, no uno—.
        """
        unseen = _candidate("Espinaca a la crema", "food", "espinaca", category="meal")

        def drop_from(*names: str) -> float:
            signals = [_signal(diego, "rejected_suggestion", "food", name, -1.0) for name in names]
            (one,) = score_candidates(
                [unseen], diego, signals, [], subject_attributes=_FOOD_ATTRIBUTES
            )
            return 0.5 - float(one["_score"])

        one_vegetable = drop_from("brocoli")
        three_vegetables = drop_from("brocoli", "coliflor", "kale")
        (itself,) = score_candidates(
            [unseen],
            diego,
            [_signal(diego, "rejected_suggestion", "food", "espinaca", -1.0)],
            [],
            subject_attributes=_FOOD_ATTRIBUTES,
        )
        point_level = 0.5 - float(itself["_score"])

        assert three_vegetables > 2 * one_vegetable
        assert one_vegetable < point_level / 3

    def test_a_category_is_never_a_veto(self, diego: User) -> None:
        """Generalizar para ordenar es útil; generalizar para vetar es otra cosa.

        `apply_signal_constraints` sigue mirando solo el sujeto puntual, y a propósito:
        sacar la espinaca de la lista porque la persona rechazó tres **otras** verduras es
        ponerle en la boca un "no" que no dijo.
        """
        rejections = [
            _signal(diego, "rejected_suggestion", "food", name, -1.0)
            for name in ("brocoli", "coliflor", "kale")
        ]
        candidates = [_candidate("Espinaca a la crema", "food", "espinaca", category="meal")]
        assert apply_signal_constraints(candidates, rejections) == candidates

    def test_a_food_the_catalogue_does_not_know_has_no_category(self, diego: User) -> None:
        """De un alimento de texto libre no sabemos la categoría, y adivinarla es el match
        difuso que la 4.4 vino a sacar."""
        rejections = [
            _signal(diego, "rejected_suggestion", "food", name, -1.0)
            for name in ("brocoli", "coliflor", "kale")
        ]
        unknown = _candidate("Tarta de acelga de la vecina", "food", "tarta de la vecina")

        (scored,) = score_candidates(
            [unknown], diego, rejections, [], subject_attributes=_FOOD_ATTRIBUTES
        )
        assert scored["_score"] == 0.5

    def test_the_index_reads_the_catalogue_including_aliases(
        self, db: Session, banana: FoodItem
    ) -> None:
        """El índice sale del catálogo, y los alias entran con la categoría del canónico.

        Importa porque el texto libre de una captura escribe el alias —"palta", no
        "avocado"— y la señal quedó guardada con **ese** nombre.
        """
        db.add(
            FoodItem(
                canonical_name="avocado",
                category="fat",
                base_unit="unit",
                aliases_json=["palta", "Aguacate"],
            )
        )
        #: Sin categoría no entra: es la fila que crea `get_or_create` con texto libre, y un
        #: `None` en el índice sería un sujeto llamado "none".
        db.add(FoodItem(canonical_name="tarta de la vecina", base_unit="unit"))
        db.flush()

        index = learning.attribute_index(db)
        assert index[("food", "banana")] == ("food_category", "fruit")
        assert index[("food", "palta")] == ("food_category", "fat")
        assert index[("food", "aguacate")] == ("food_category", "fat")
        assert ("food", "tarta de la vecina") not in index

    def test_the_index_reads_the_exercise_catalogue_by_muscle_group(self, db: Session) -> None:
        """La otra mitad del índice (4.5.8): un ejercicio generaliza a su grupo muscular.

        Tres casos en una: el grupo canónico entra, el alias del grupo entra **normalizado**
        —`triceps` es `arms` desde que `MUSCLE_GROUPS` es único—, y una fila cuyo grupo no cae
        en el vocabulario no entra. Ese último no es un descarte por prolijidad: un balde de
        atributo con un grupo que ningún otro lado nombra no generaliza a nada, porque nunca va
        a haber un segundo ejercicio adentro.
        """
        db.add_all(
            [
                ExerciseType(name="Bench Press", category="strength", muscle_group="chest"),
                ExerciseType(name="Push-ups", category="strength", muscle_group="CHEST"),
                ExerciseType(name="Triceps Dip", category="strength", muscle_group="triceps"),
                ExerciseType(name="Eyebrow Raise", category="other", muscle_group="eyebrows"),
                ExerciseType(name="Walk", category="cardio", muscle_group=None),
            ]
        )
        db.flush()

        index = learning.attribute_index(db)
        assert index[("exercise", "bench press")] == ("muscle_group", "chest")
        assert index[("exercise", "push ups")] == ("muscle_group", "chest")
        assert index[("exercise", "triceps dip")] == ("muscle_group", "arms")
        assert ("exercise", "eyebrow raise") not in index
        assert ("exercise", "walk") not in index

    def test_the_exercise_index_reads_aliases_too(self, db: Session) -> None:
        """Desde la 7.5, el alias en castellano cae en el mismo balde que su canónico.

        Espejo de `test_the_index_reads_the_catalogue_including_aliases`: el texto libre de
        una captura o de un motivo escribe "press de banca", no "Bench Press", y la señal
        queda guardada con ese nombre.
        """
        db.add(
            ExerciseType(
                name="Bench Press",
                category="strength",
                muscle_group="chest",
                aliases_json=["press de banca"],
            )
        )
        db.flush()

        index = learning.attribute_index(db)
        assert index[("exercise", "bench press")] == ("muscle_group", "chest")
        assert index[("exercise", "press de banca")] == ("muscle_group", "chest")

    def test_rejecting_two_chest_exercises_moves_a_third(self, diego: User) -> None:
        """El mismo cuento que las verduras, un dominio más allá — y es el camino que existe.

        Una tarjeta de catálogo declara `("exercise", row.name)` y el feedback se guarda contra
        esa misma clave, así que las dos puntas se encuentran acá aunque las capturas en
        castellano no lleguen nunca al índice.
        """
        rejections = [
            _signal(diego, "rejected_suggestion", "exercise", name, -1.0)
            for name in ("bench press", "push ups")
        ]
        unseen = _candidate("Incline Press", "exercise", "incline press")

        (blind,) = score_candidates([unseen], diego, rejections, [])
        (taught,) = score_candidates(
            [unseen], diego, rejections, [], subject_attributes=_EXERCISE_ATTRIBUTES
        )
        assert blind["_score"] == 0.5, "sin el índice, un ejercicio sin señales no se mueve"
        assert taught["_score"] < 0.5

    def test_a_muscle_group_signal_does_not_fill_its_own_bucket(self, diego: User) -> None:
        """La asimetría documentada del índice, medida para que no se rompa en silencio.

        Un grupo no es una clave del índice, así que las señales que una captura de
        entrenamiento escribe con `("muscle_group", …)` pesan en el nivel **puntual** y no en el
        balde del atributo. Hacerlas entrar pide una entrada que mapee un grupo a sí mismo, que
        es la extensión anotada en el plan: si alguien la agrega, este test es el que dice que
        la asimetría era deliberada y que hay que decidirla de nuevo, no borrarla de paso.
        """
        trained = [
            _signal(diego, "repeated_activity", "muscle_group", "chest", 1.0, age_days=30)
        ] * 12
        unseen = _candidate("Incline Press", "exercise", "incline press")

        (scored,) = score_candidates(
            [unseen], diego, trained, [], subject_attributes=_EXERCISE_ATTRIBUTES
        )
        assert scored["_score"] == 0.5

    def test_the_attribute_type_is_not_a_recordable_subject(self, db: Session, diego: User) -> None:
        """`food_category` existe solo como atributo: no hay fila con ese tipo.

        La otra opción era grabar una segunda señal por comida con la categoría del alimento
        —lo que la 4.4.1 dejó anotado— y es peor: congelaría la categoría del día en que se
        comió, y solo aprendería de las comidas futuras. Derivar en cada lectura es
        retroactivo y se corrige solo.
        """
        assert "food_category" in learning.ATTRIBUTE_SUBJECT_TYPES
        assert "food_category" not in learning.SUBJECT_TYPES
        with pytest.raises(ValueError, match="food_category"):
            learning.record_signal(
                db,
                user_id=diego.id,
                signal_type="rejected_suggestion",
                subject_type="food_category",
                subject_name="vegetable",
                value=-1.0,
                source_type="explicit",
            )

    def test_being_an_attribute_does_not_make_a_type_unrecordable(self) -> None:
        """Y la vuelta, que la 4.5.8 hizo posible: `muscle_group` es atributo **y** sujeto.

        `ATTRIBUTE_TYPES` es el vocabulario del nivel; `ATTRIBUTE_SUBJECT_TYPES` son los que
        existen *solo* ahí, y se deriva restando. La diferencia no es de vocabulario: una
        captura de entrenamiento escribe filas `("muscle_group", …)`, así que un grupo tiene
        señales propias, sale como sujeto en el panel de `/profile/` con su botón de olvido, y
        además puede aparecer como conclusión de categoría por lo que se opinó de los ejercicios
        de ese grupo. Leer el conjunto derivado como "los tipos del nivel atributo" es lo que
        haría que alguien saque el grupo de `SUBJECT_TYPES` y calle el nivel puntual entero.
        """
        assert "muscle_group" in learning.ATTRIBUTE_TYPES
        assert "muscle_group" in learning.SUBJECT_TYPES
        assert "muscle_group" not in learning.ATTRIBUTE_SUBJECT_TYPES

    def test_the_declared_attribute_vocabulary_is_the_one_the_index_emits(
        self, db: Session, banana: FoodItem
    ) -> None:
        """`ATTRIBUTE_TYPES` tiene que ser exactamente lo que `attribute_index` produce.

        Es el conjunto que el panel de `/profile/` recorre para saber rotular una conclusión
        (`components/domain.html`), así que separarlo del índice tiene dos formas de doler: un
        tipo declarado y nunca producido es un rótulo que nadie ve, y un tipo producido y no
        declarado sale en pantalla con el nombre crudo de la columna. Con un catálogo que tiene
        las dos puntas sembradas, la igualdad se puede afirmar de verdad.
        """
        db.add(ExerciseType(name="Bench Press", category="strength", muscle_group="chest"))
        db.flush()

        emitted = {attribute_type for attribute_type, _ in learning.attribute_index(db).values()}
        assert emitted == learning.ATTRIBUTE_TYPES


class TestTimeOfDayLearning:
    """Aprender *cuándo* le gusta algo, no solo qué (4.4.5).

    El café del desayuno y el café de la cena son el mismo sujeto con dos respuestas
    distintas, y hasta acá la app las promediaba en un solo número. La franja sale del
    `meal_type` que `MealService.log_meal` mete en `context_json` desde la 4.4.1, así que
    este eje aprende de lo implícito: `Suggestion` no tiene columna de contexto y agregarla
    sería una migración que la v3 no tiene disponible.
    """

    @staticmethod
    def _ate(diego: User, name: str, slot: str, times: int) -> list[Any]:
        return [
            _signal(
                diego,
                "repeated_meal_choice",
                "food",
                name,
                1.0,
                source_type="implicit",
                context={"meal_type": slot},
            )
        ] * times

    def test_coffee_at_breakfast_not_at_dinner(self, diego: User) -> None:
        """El ejemplo del plan, palabra por palabra."""
        history = self._ate(diego, "cafe", "breakfast", 20)
        morning = _candidate("Café", "food", "cafe", category="meal", meal_type="breakfast")
        evening = _candidate("Café", "food", "cafe", category="meal", meal_type="dinner")

        (at_breakfast,) = score_candidates([morning], diego, history, [])
        (at_dinner,) = score_candidates([evening], diego, history, [])
        assert at_dinner["_score"] < 0.5 < at_breakfast["_score"]

    def test_a_food_eaten_at_every_hour_is_indifferent_to_the_hour(self, diego: User) -> None:
        """Un plato que se come a cualquier hora no debería moverse por la hora.

        Es lo que obliga a que esto sea una **diferencia** entre franjas y no el promedio
        dentro de una: la milanesa gusta —y eso ya lo cobra el nivel puntual—, pero no
        gusta *más al almuerzo*.
        """
        history = self._ate(diego, "milanesa", "lunch", 10) + self._ate(
            diego, "milanesa", "dinner", 10
        )
        at_lunch = _candidate("Milanesas", "food", "milanesa", category="meal", meal_type="lunch")
        no_slot = _candidate("Milanesas", "food", "milanesa", category="meal")

        (with_slot,) = score_candidates([at_lunch], diego, history, [])
        (without_slot,) = score_candidates([no_slot], diego, history, [])
        assert with_slot["_score"] == without_slot["_score"]

    def test_one_observation_is_a_hint_and_twenty_are_a_rule(self, diego: User) -> None:
        """La confianza por evidencia de la 4.4.3 vale igual acá: una vez no es un hábito."""
        candidate = _candidate("Café", "food", "cafe", category="meal", meal_type="breakfast")

        (once,) = score_candidates([candidate], diego, self._ate(diego, "cafe", "breakfast", 1), [])
        (often,) = score_candidates(
            [candidate], diego, self._ate(diego, "cafe", "breakfast", 20), []
        )
        assert 0.5 < once["_score"] < often["_score"]

    def test_a_meal_without_an_hour_says_nothing_about_the_hour(self, diego: User) -> None:
        """`"other"` es el default de la columna, no una franja.

        Contarlo como una franja más haría que cada comida sin hora argumentara contra todas
        las franjas reales — un plato registrado sin hora bajaría de score a todas las horas
        por el solo hecho de estar registrado.
        """
        untagged = [
            _signal(diego, "repeated_meal_choice", "food", "pollo", 1.0, source_type="implicit")
        ] * 8
        as_other = self._ate(diego, "pollo", "other", 8)
        candidate = _candidate(
            "Pollo al horno", "food", "pollo", category="meal", meal_type="lunch"
        )

        (without_context,) = score_candidates([candidate], diego, untagged, [])
        (with_other,) = score_candidates([candidate], diego, as_other, [])
        assert with_other["_score"] == without_context["_score"]
        assert learning.slot_affinities(as_other) == {}

    def test_the_contrast_never_exceeds_one_knob(self, diego: User) -> None:
        """La resta de dos fuerzas vive en `[-2, 2]`; sin el recorte, un eje movería el doble.

        Con evidencia grande a los dos lados —café al desayuno, rechazos de café a la cena—
        `in_slot` tiende a `+1` y `off_slot` a `−1`, así que la resta se va a `+2`.
        """
        history = (
            self._ate(diego, "cafe", "breakfast", 40)
            + [
                _signal(
                    diego,
                    "rejected_suggestion",
                    "food",
                    "cafe",
                    -1.0,
                    context={"meal_type": "dinner"},
                )
            ]
            * 40
        )
        contrast = learning.slot_contrast(
            ("food", "cafe"), "breakfast", slots=learning.slot_affinities(history)
        )
        assert contrast == pytest.approx(1.0)

    def test_an_hour_is_never_a_veto(self, diego: User) -> None:
        """Que nunca se haya registrado un café a la cena no es un "no".

        Es la misma razón por la que el nivel atributo no filtra, más una más fuerte: acá
        lo que hay es una **ausencia**, y usar la ausencia como señal es lo que la 4.4.3
        dejó postergado a propósito, junto con el umbral mínimo de evidencia para filtrar.
        """
        history = self._ate(diego, "cafe", "breakfast", 40)
        candidate = _candidate("Café", "food", "cafe", category="meal", meal_type="dinner")

        assert apply_signal_constraints([candidate], history) == [candidate]
        (scored,) = score_candidates([candidate], diego, history, [])
        assert scored["_score"] > 0.0

    def test_the_generator_declares_the_slot_it_is_offering(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """El scorer no re-deriva la hora: la lee del candidato.

        Re-derivarla sería duplicar las ventanas horarias de `_current_meal_type` y
        discrepar con el generador justo cuando alguien pasa `meal_type` a mano — que es
        exactamente lo que hace este test.
        """
        from app.recommendations.generators import meal_generator

        food = FoodItem(canonical_name="avena", base_unit="g", category="grain")
        db.add(food)
        db.flush()
        db.add(
            PantryStock(
                household_id=household.id,
                food_item_id=food.id,
                current_quantity=100,
                unit="g",
                low_stock_threshold=500,
            )
        )
        db.flush()

        candidates = meal_generator.generate(
            db, diego, [], build_user_context(db, diego), meal_type="breakfast"
        )
        assert candidates, "el generador no produjo candidatos con stock cargado"
        assert {learning.candidate_slot(c) for c in candidates} == {"breakfast"}


class TestSatiety:
    """Separar "me gusta" de "lo comí ayer" (4.4.6).

    Los tres ejes anteriores responden todos la misma pregunta —¿le gusta?— con distinto
    nivel de detalle. Este no: las mismas filas que dicen "esto le gusta" dicen también
    "esto lo comió ayer", y hasta acá solo se leía la primera, así que el alimento de todos
    los días acumulaba decenas de positivos y el bucle empujaba a repetir en vez de a
    variar. Lo que separa las dos lecturas no es el dato: es el reloj —semivida de día y
    medio contra veintiuno—.
    """

    @staticmethod
    def _ate(user: User, name: str, times: int, *, age_days: float) -> list[Any]:
        return [
            _signal(
                user,
                "repeated_meal_choice",
                "food",
                name,
                1.0,
                source_type="implicit",
                age_days=age_days,
            )
        ] * times

    def test_a_favourite_eaten_yesterday_loses_to_one_that_was_not(
        self, db: Session, diego: User
    ) -> None:
        """El caso que motiva todo el eje.

        Dos alimentos igual de queridos, con la misma historia; a uno lo comió ayer. Sin
        saciedad los dos salían con el mismo score y el orden lo decidía el generador.
        """
        history = self._ate(diego, "milanesa", 8, age_days=30) + self._ate(
            diego, "guiso", 8, age_days=30
        )
        history += self._ate(diego, "milanesa", 1, age_days=1)
        candidates = [
            _candidate("Milanesas", "food", "milanesa", category="meal"),
            _candidate("Guiso de lentejas", "food", "guiso", category="meal"),
        ]

        scored = score_candidates(candidates, diego, history, [])
        assert scored[0]["title"] == "Guiso de lentejas"
        #: Y sigue siendo un favorito: baja del primer puesto, no de la lista.
        assert scored[1]["_score"] > 0.5

    def test_a_favourite_not_eaten_lately_is_still_a_favourite(
        self, db: Session, diego: User
    ) -> None:
        """La otra mitad del reparto, y la razón por la que la perilla no es más grande.

        La saciedad tiene que poder cancelar el boost acumulado de hoy y nada más: en una
        semana ya se apagó y lo que queda es el gusto entero.
        """
        history = self._ate(diego, "milanesa", 8, age_days=30)
        candidate = _candidate("Milanesas", "food", "milanesa", category="meal")

        (never,) = score_candidates([candidate], diego, history, [])
        (yesterday,) = score_candidates(
            [candidate], diego, history + self._ate(diego, "milanesa", 1, age_days=1), []
        )
        (last_week,) = score_candidates(
            [candidate], diego, history + self._ate(diego, "milanesa", 1, age_days=7), []
        )

        assert yesterday["_score"] < never["_score"]
        #: Una comida más hace una semana solo agrega gusto: a siete días son casi cinco
        #: semividas de saciedad, y lo que queda de la presión es ruido.
        assert last_week["_score"] > never["_score"]

    def test_satiety_fades_in_days_and_a_taste_in_weeks(self, diego: User) -> None:
        """Los dos relojes, medidos en la misma fila.

        Es la afirmación central de la 4.4.6 y la única forma de que no se vuelva a fundir
        en una sola suma: si estas dos semividas fueran la misma, el eje no existiría.
        """
        ate_it = self._ate(diego, "milanesa", 3, age_days=3)

        (taste,) = learning.subject_affinities(ate_it).values()
        pressure = learning.satiety_pressure(ate_it)[("food", "milanesa")]

        #: A tres días el gusto perdió un 10% y la saciedad, el 75%.
        assert taste.evidence == pytest.approx(3 * 0.5 ** (3 / 21), abs=1e-3)
        assert pressure < 0.3
        #: Y a dos semanas la presión no se apagó por un corte —no hay ninguno— sino porque
        #: son casi diez semividas: lo que queda no llega a mover el score ni al redondeo.
        stale = learning.satiety_pressure(self._ate(diego, "milanesa", 3, age_days=14))
        assert stale[("food", "milanesa")] < 0.01

    def test_saying_you_like_something_does_not_fill_you_up(self, diego: User) -> None:
        """La distinción entre un acto y un dicho, que es lo que decide qué filas cuentan.

        Marcar "me gusta el pescado" en el perfil, o aceptar la sugerencia de comerlo, no es
        haberlo comido: sube el gusto y no produce nada de presión. Lo contrario haría que
        contar una preferencia la suprimiera.
        """
        said = [
            _signal(diego, "explicit_preference", "food", "pescado", 1.0),
            _signal(diego, "accepted_suggestion", "food", "pescado", 1.0),
        ]
        assert learning.satiety_pressure(said) == {}
        assert learning.CONSUMPTION_SIGNAL_TYPES <= learning.POSITIVE_SIGNAL_TYPES

    def test_an_act_fills_you_up_whatever_the_act_was(self, diego: User) -> None:
        """Comer, comprar y entrenar cuentan los tres.

        Comprar leche ayer es una razón para no sugerir comprar leche hoy, y repetir el
        mismo ejercicio tres días seguidos es el mismo error con otro cuerpo.
        """
        for signal_type, subject_type in (
            ("repeated_meal_choice", "food"),
            ("repeated_purchase", "food"),
            ("repeated_activity", "exercise"),
        ):
            signal = _signal(diego, signal_type, subject_type, "algo", 1.0, age_days=0.5)
            assert learning.satiety_pressure([signal]), signal_type

    def test_satiety_is_never_a_veto(self, db: Session, diego: User) -> None:
        """Haber comido milanesas ayer no es un "no" a las milanesas.

        Es la misma razón que en el nivel atributo y en la franja horaria: lo que filtra es
        el rechazo explícito, y nada más. Acá además la presión se apaga sola en un par de
        días, así que un veto duraría más que su propia causa.
        """
        history = self._ate(diego, "milanesa", 30, age_days=0.5)
        candidate = _candidate("Milanesas", "food", "milanesa", category="meal")

        assert apply_signal_constraints([candidate], history) == [candidate]
        (scored,) = score_candidates([candidate], diego, history, [])
        assert scored["_score"] > 0.0

    def test_pressure_saturates_instead_of_growing(self, diego: User) -> None:
        """Treinta raciones no pueden restar diez veces lo que restan tres.

        Misma curva `n/(n+k)` que la confianza del gusto, y por la misma razón: sin ella la
        penalización se desbordaría y un alimento frecuente quedaría suprimido para siempre
        —el problema original con el signo dado vuelta—.
        """
        three = learning.satiety_pressure(self._ate(diego, "milanesa", 3, age_days=0.5))
        thirty = learning.satiety_pressure(self._ate(diego, "milanesa", 30, age_days=0.5))
        key = ("food", "milanesa")

        assert three[key] < thirty[key] < 1.0
        assert thirty[key] < 2 * three[key]

    def test_satiety_is_not_the_diversity_penalty(self, db: Session, diego: User) -> None:
        """Dos cosas que se parecen y miden lo opuesto.

        La penalización por diversidad mira lo que la **app sugirió** y es un escalón fijo
        por siete días; la saciedad mira lo que la **persona hizo** y se apaga sola. Una
        sugerencia que la app nunca hizo no lleva la primera, aunque la comida haya pasado.
        """
        history = self._ate(diego, "milanesa", 2, age_days=0.5)
        candidate = _candidate("Milanesas", "food", "milanesa", category="meal")

        (scored,) = score_candidates([candidate], diego, history, [])
        assert scored["_score"] > 0.5 - scorer._DIVERSITY_PENALTY


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

    def test_one_unused_suggestion_is_not_enough_to_drop_a_candidate(
        self, db: Session, diego: User
    ) -> None:
        """Una ausencia sola no puede vetar, y ese es todo el punto del umbral.

        La ausencia es el negativo más barato de producir —lo escribe un job, no una
        persona— y el más fácil de producir mal: una tarjeta que nadie miró queda igual de
        "sin usar" que una que se descartó a propósito. Si alcanzara para filtrar, un solo
        día en que la persona no abrió la app le sacaría un alimento de las sugerencias.
        """
        signals = [
            _signal(
                diego,
                "unused_suggestion",
                "food",
                "lentejas",
                learning.ABSENCE_VALUE,
                source_type="inferred",
            )
        ]
        candidates = [_candidate("Guiso de lentejas", "food", "lentejas", category="meal")]

        assert len(apply_signal_constraints(candidates, signals)) == 1
        #: Pero pesa: no se filtra, se ordena más abajo. Es la distinción entera entre los
        #: dos mecanismos.
        (scored,) = score_candidates(candidates, diego, signals, [])
        assert scored["_score"] < 0.5

    def test_three_unused_suggestions_do_drop_it(self, db: Session, diego: User) -> None:
        """Tres veces ya no es "no la vio", es un patrón — y ahí sí sale de la lista."""
        signals = [
            _signal(
                diego,
                "unused_suggestion",
                "food",
                "lentejas",
                learning.ABSENCE_VALUE,
                source_type="inferred",
            )
            for _ in range(3)
        ]
        candidates = [_candidate("Guiso de lentejas", "food", "lentejas", category="meal")]

        assert apply_signal_constraints(candidates, signals) == []

    def test_stale_absences_do_not_pile_up_into_a_veto(self, db: Session, diego: User) -> None:
        """El umbral de cantidad no derogó el de frescura: se piden los dos.

        Sin esto, un sujeto que se sugirió durante meses acumularía ausencias hasta vetarse
        para siempre — que es el veto permanente que la 4.4.2 vino a sacar, entrando por la
        puerta de al lado.
        """
        signals = [
            _signal(
                diego,
                "unused_suggestion",
                "food",
                "lentejas",
                learning.ABSENCE_VALUE,
                source_type="inferred",
                age_days=90,
            )
            for _ in range(10)
        ]
        candidates = [_candidate("Guiso de lentejas", "food", "lentejas", category="meal")]

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


class TestNotNowMeansNotNow:
    """La 4.4.7: que "Ahora no" produzca silencio y no una repetición.

    Antes de esto, la única cosa que reservaba el lugar de un sujeto era una sugerencia
    **pendiente**. Posponer respondía la fila, o sea la sacaba de `pending`, así que el job
    —7:40 y 18:40 locales, `scheduler._SCHEDULE`— volvía a escribir la misma tarjeta con el
    score apenas más bajo por la señal de descarte. El botón prometía silencio y entregaba la
    misma cosa en la corrida siguiente, o sea el mismo día.

    Los dos lados se prueban por separado porque son dos módulos: el servicio escribe
    `snoozed_until` y el motor lo lee al decidir qué persiste.
    """

    @staticmethod
    def _card(db: Session, user: User, **overrides: Any) -> Any:
        from app.models.suggestion import Suggestion

        defaults: dict[str, Any] = {
            "scope_type": "user",
            "household_id": user.household_id,
            "scope_user_id": user.id,
            "category": "activity",
            "subject_type": "exercise",
            "subject_name": "biking",
            "title": "Salí en bici",
            "text": "30 minutos.",
            "rationale": "Te gusta.",
            "source_type": "rule",
            "status": "pending",
        }
        defaults.update(overrides)
        card = Suggestion(**defaults)
        db.add(card)
        db.flush()
        return card

    @staticmethod
    def _respond(db: Session, user: User, card: Any, status: str) -> None:
        from app.schemas.suggestion import SuggestionFeedback
        from app.services.suggestion_service import SuggestionService

        SuggestionService(db).respond_to_suggestion(
            card.id, SuggestionFeedback(status=status), user.id, user.household_id
        )

    @staticmethod
    def _engine() -> Any:
        from app.recommendations.engine import RecommendationEngine

        return RecommendationEngine()

    def test_postponing_writes_a_window_instead_of_just_answering_the_row(
        self, db: Session, diego: User
    ) -> None:
        from app.services.suggestion_service import SuggestionService

        card = self._card(db, diego)
        self._respond(db, diego, card, "snoozed")

        assert card.status == "snoozed"
        assert card.snoozed_until is not None
        #: `as_utc` y no una comparación directa: el servicio commitea, así que el valor
        #: vuelve leído de la base, y SQLite —la base de los tests— no guarda el offset.
        #: En Postgres la columna es `timestamptz` y vuelve aware. Es una diferencia de la
        #: base de prueba, no del código, y `as_utc` existe justamente para no repetir el
        #: `.replace(tzinfo=...)` que en Postgres *corre* el instante en vez de convertirlo.
        remaining = clock.as_utc(card.snoozed_until) - datetime.now(timezone.utc)
        assert remaining > timedelta(days=SuggestionService._SNOOZE_DAYS - 0.01)

    def test_the_window_outlasts_a_job_cycle_and_stays_under_the_diversity_step(
        self,
    ) -> None:
        """Los dos bordes de `_SNOOZE_DAYS`, que es un número y necesita defensa.

        Por debajo del hueco entre dos corridas del job la ventana sería invisible —la
        tarjeta volvería a escribirse igual—, y por encima de la ventana de diversidad se
        saltaría el escalón siguiente: la idea es que el sujeto primero no aparezca, después
        aparezca más abajo, y al final vuelva a competir de igual a igual.

        El piso se **deriva del horario** en vez de escribirse a mano: el job corre a horas
        locales fijas (`scheduler._SCHEDULE`, desde la 4.1) y no cada N horas, así que un
        comentario con un intervalo se desactualiza en silencio la próxima vez que el horario
        cambie. Lo que tiene que superar la ventana es el hueco **más largo** entre dos
        corridas, que es el peor caso para que el silencio se note.
        """
        from app.jobs.scheduler import _SCHEDULE
        from app.services.suggestion_service import SuggestionService

        hours = sorted(int(h) for h in _SCHEDULE["suggestion_generation"][0].split(","))
        gaps = [b - a for a, b in zip(hours, hours[1:], strict=False)]
        gaps.append(24 - hours[-1] + hours[0])

        assert timedelta(days=SuggestionService._SNOOZE_DAYS) > timedelta(hours=max(gaps))
        assert SuggestionService._SNOOZE_DAYS < scorer._RECENT_SUGGESTION_DAYS

    def test_a_postponed_subject_is_not_offered_again(self, db: Session, diego: User) -> None:
        card = self._card(db, diego)
        self._respond(db, diego, card, "snoozed")

        engine = self._engine()
        suppressed = engine._suppressed_subjects_for_user(db, diego.id)
        assert ("exercise", "biking") in suppressed

        candidates = [
            _candidate("Salí en bici de nuevo", "exercise", "biking"),
            _candidate("Salí a correr", "exercise", "running"),
        ]
        kept = engine._without_duplicate_subjects(candidates, suppressed, 10)
        assert [c["subject_name"] for c in kept] == ["running"]

    def test_when_the_window_expires_the_subject_competes_again(
        self, db: Session, diego: User
    ) -> None:
        """No hace falta ningún job que "resucite" nada: se compara con el reloj.

        La fila se queda en `snoozed` para siempre y el sujeto se destraba solo cuando
        `snoozed_until` queda en el pasado. Un job de rehabilitación sería una pieza más que
        puede no correr, y el estado que dejaría es el que esta consulta ya deduce.
        """
        card = self._card(db, diego)
        self._respond(db, diego, card, "snoozed")
        card.snoozed_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.flush()

        engine = self._engine()
        assert engine._suppressed_subjects_for_user(db, diego.id) == set()
        #: Y cuando vuelve, vuelve más abajo: la penalización por diversidad de los 7 días
        #: es el escalón siguiente, y esa fila sigue estando entre las recientes.
        candidate = _candidate("Salí en bici", "exercise", "biking", confidence=0.8)
        (scored,) = score_candidates([candidate], diego, [], [card])
        assert scored["_score"] < 0.8

    def test_a_pending_suggestion_still_holds_the_place_of_its_subject(
        self, db: Session, diego: User
    ) -> None:
        """La mitad que ya existía antes de la 4.4.7, y que no se perdió al agregar la otra."""
        self._card(db, diego)
        assert ("exercise", "biking") in self._engine()._suppressed_subjects_for_user(db, diego.id)

    def test_accepting_does_not_silence_anything(self, db: Session, diego: User) -> None:
        """Aceptar no suprime: el candidato ya se cumplió y puede volver a proponerse.

        Es la diferencia con las otras dos respuestas. Salir en bici hoy porque la app lo
        sugirió es la mejor razón para que lo vuelva a sugerir la semana que viene.
        """
        card = self._card(db, diego)
        self._respond(db, diego, card, "accepted")

        assert card.snoozed_until is None
        assert self._engine()._suppressed_subjects_for_user(db, diego.id) == set()

    def test_rejecting_leans_on_the_filter_and_not_on_the_window(
        self, db: Session, diego: User
    ) -> None:
        """`rejected` no necesita ventana porque tiene algo mucho más largo.

        `learning.rejected_subjects` lo saca de la lista mientras el "no" conserve la mitad
        de su peso —90 días de semivida—, así que una ventana de 3 días encima de eso no
        agregaría nada. Lo que este test fija es que la elección fue esa y no un olvido.
        """
        from app.recommendations.filters import apply_signal_constraints

        card = self._card(db, diego)
        self._respond(db, diego, card, "rejected")
        assert card.snoozed_until is None

        from app.models.signal import BehaviorSignal

        signals = db.query(BehaviorSignal).filter(BehaviorSignal.user_id == diego.id).all()
        candidate = _candidate("Salí en bici", "exercise", "biking")
        assert apply_signal_constraints([candidate], signals) == []

    def test_the_other_members_silence_is_not_yours(
        self, db: Session, diego: User, rocio: User
    ) -> None:
        """Regla 4 de `AGENTS.md`, en el lado de la lectura.

        Rocío y Diego comparten `household_id`, así que una consulta que se olvide del
        `scope_user_id` deja que el "ahora no" de una persona calle las sugerencias de la
        otra — el mismo bug que tenía el `or_` de las notificaciones.
        """
        card = self._card(db, rocio, scope_user_id=rocio.id)
        self._respond(db, rocio, card, "snoozed")

        engine = self._engine()
        assert engine._suppressed_subjects_for_user(db, rocio.id) == {("exercise", "biking")}
        assert engine._suppressed_subjects_for_user(db, diego.id) == set()

    def test_a_personal_snooze_does_not_silence_the_households_shopping_list(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """La contraparte: la consulta de hogar filtra además por `scope_type`.

        Las sugerencias personales también llevan `household_id`, así que sin esa condición
        una tarjeta personal pospuesta bloquearía la de despensa del mismo sujeto — que es
        de la casa y de nadie en particular.
        """
        card = self._card(db, diego, category="meal", subject_type="food", subject_name="leche")
        self._respond(db, diego, card, "snoozed")

        engine = self._engine()
        assert engine._suppressed_subjects_for_household(db, household.id) == set()
        assert engine._suppressed_subjects_for_user(db, diego.id) == {("food", "leche")}


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
        candidates = meal_generator.generate(db, diego, preferences, build_user_context(db, diego))
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
        #: Sin entrenamientos: empujón de constancia + rotación + preferida. La cuarta era
        #: una de las ocho actividades hardcodeadas, y desde la 4.5.2 la tarjeta con nombre de
        #: ejercicio sale del catálogo —que en los tests está vacío a propósito—. Que con el
        #: catálogo sembrado aparezcan las de catálogo lo cubre `test_activity_generator.py`.
        candidates = activity_generator.generate(diego, preferences, build_user_context(db, diego))
        self._assert_subjects(candidates, 3)

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

        candidates = activity_generator.generate(diego, [], build_user_context(db, diego))
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
        #: Un panel **fresco y con fecha**: es la única banda en la que el generador aconseja
        #: sobre los marcadores. Antes acá había un panel sin fecha (`age_days=None`) y el
        #: generador producía las mismas cinco tarjetas, que es justo lo que la 4.5.6 dejó de
        #: hacer: la antigüedad ahora decide. Las otras tres bandas se miden en
        #: `TestBloodPanelBands`.
        panel = _panel(blood_values, age_days=7)
        candidates = blood_generator.generate(diego, panel)
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


class TestPantryCardsCiteWhatTheyMeasured:
    """Que las cuatro tarjetas de despensa expliquen con números de esta corrida.

    Hasta la 4.5.7 las cuatro razones eran frases de catálogo —"Items at zero stock may
    block meal preparation."— idénticas para toda tarjeta de esa regla. El test de AST
    (`test_explain.py::TestNoFixedRationalesLeft`) impide que vuelva una constante; estos
    miden lo que ese no puede: que el número que se cita sea **el que se midió**. Un
    f-string puede interpolar cualquier cosa y pasar igual.
    """

    @staticmethod
    def _food(db: Session, name: str) -> FoodItem:
        food = FoodItem(canonical_name=name, category="other", base_unit="g")
        db.add(food)
        db.flush()
        return food

    @classmethod
    def _stock(
        cls,
        db: Session,
        household: Household,
        name: str,
        *,
        quantity: float,
        threshold: float | None = None,
    ) -> FoodItem:
        food = cls._food(db, name)
        db.add(
            PantryStock(
                household_id=household.id,
                food_item_id=food.id,
                current_quantity=quantity,
                unit="g",
                low_stock_threshold=threshold,
            )
        )
        db.flush()
        return food

    @staticmethod
    def _purchases(
        db: Session, household: Household, food: FoodItem, *, days_ago: list[int]
    ) -> None:
        for offset in days_ago:
            db.add(
                PantryMovement(
                    household_id=household.id,
                    user_id=None,
                    food_item_id=food.id,
                    movement_type="purchase",
                    quantity=1,
                    unit="g",
                    timestamp=datetime.now(tz=timezone.utc) - timedelta(days=offset),
                )
            )
        db.flush()

    @staticmethod
    def _card(candidates: list[dict[str, Any]], subject_name: str) -> dict[str, Any]:
        matching = [c for c in candidates if c["subject_name"] == subject_name]
        assert len(matching) == 1, f"se esperaba una tarjeta {subject_name!r}, hay {len(matching)}"
        return matching[0]

    @staticmethod
    def _generate(db: Session, household: Household) -> list[dict[str, Any]]:
        return pantry_generator.generate(db, household.id)

    def test_the_out_of_stock_card_counts_the_zeros_against_the_whole_pantry(
        self, db: Session, household: Household
    ) -> None:
        """Dos en cero de cuatro seguidos: los dos números salen del stock leído."""
        self._stock(db, household, "leche", quantity=0, threshold=500)
        self._stock(db, household, "arroz", quantity=0, threshold=200)
        self._stock(db, household, "avena", quantity=900, threshold=200)
        self._stock(db, household, "aceite", quantity=900, threshold=200)

        card = self._card(self._generate(db, household), "out of stock alert")

        assert "2 of the 4 items" in card["rationale"]

    def test_the_low_stock_card_names_the_one_with_the_least_margin(
        self, db: Session, household: Household
    ) -> None:
        """El que menos margen tiene sobre su umbral, con sus dos números.

        No es "el primero de la lista": la razón nombra a `avena`, que está 10 por debajo de
        su umbral, y no a `banana`, que está justo en el suyo — aunque el orden alfabético de
        la consulta ponga a `avena` antes y el test siga verde por accidente si se elige mal.
        """
        self._stock(db, household, "banana", quantity=3, threshold=3)
        self._stock(db, household, "avena", quantity=190, threshold=200)

        card = self._card(self._generate(db, household), "low stock alert")

        assert "avena is down to 190 g against a threshold of 200" in card["rationale"]
        assert "the tightest of the 2 items" in card["rationale"]

    def test_the_regulars_card_cites_how_many_times_the_top_one_was_bought(
        self, db: Session, household: Household
    ) -> None:
        """Tres compras de café contra dos de yerba: la razón nombra al más comprado."""
        cafe = self._food(db, "cafe")
        yerba = self._food(db, "yerba")
        self._purchases(db, household, cafe, days_ago=[3, 10, 20])
        self._purchases(db, household, yerba, days_ago=[4, 11])

        card = self._card(self._generate(db, household), "restock regulars")

        assert "cafe was bought 3 times in the last 60 days" in card["rationale"]

    def test_the_co_purchase_card_says_same_day_because_that_is_what_it_grouped(
        self, db: Session, household: Household
    ) -> None:
        """La medición es un agrupado por día, así que la razón no puede decir "juntos".

        Y nombra cuál de los dos está en cero, que es la parte accionable: el par explica por
        qué se propone, el faltante es lo que hay que comprar.
        """
        cafe = self._stock(db, household, "cafe", quantity=0, threshold=100)
        azucar = self._stock(db, household, "azucar", quantity=900, threshold=100)
        self._purchases(db, household, cafe, days_ago=[3, 10])
        self._purchases(db, household, azucar, days_ago=[3, 10])

        card = self._card(self._generate(db, household), "cafe")

        assert "on the same day 2 times" in card["rationale"]
        assert "cafe is the one at zero" in card["rationale"]
        assert azucar.canonical_name in card["rationale"]

    def test_an_item_low_without_a_threshold_is_not_called_low(
        self, db: Session, household: Household
    ) -> None:
        """Sin umbral cargado no hay "poco": 1 g de sal puede ser una vida entera de sal.

        Lo cuida `PantryStock.is_low`, que es de donde la tarjeta lee la regla desde la
        4.5.7 en vez de tener su propia copia de la comparación.
        """
        self._stock(db, household, "sal", quantity=1, threshold=None)

        assert self._generate(db, household) == []


class TestBloodPanelBands:
    """Qué hace el generador de sangre según de cuándo sea el panel.

    Hasta la 4.5.6 la respuesta era "lo mismo siempre": `analysis_date` estaba en el modelo,
    la antigüedad no se leía en ningún lado, y un panel de hace tres años dictaba el consejo
    de hoy con la misma confianza que uno de la semana pasada. Ahora hay cuatro bandas y cada
    una tiene una conducta distinta, así que hay un caso por banda: sin eso, borrar el umbral
    dejaría la suite en verde.
    """

    _ABNORMAL = {
        "hemoglobin": {"value": 10.1, "unit": "g/dL", "status": "low"},
        "ldl": {"value": 190, "unit": "mg/dL", "status": "critical_high"},
    }
    _ALL_NORMAL = {
        "hemoglobin": {"value": 14.2, "unit": "g/dL", "status": "normal"},
        "glucose": {"value": 90, "unit": "mg/dL", "status": "normal"},
    }

    @staticmethod
    def _generate(diego: User, values: dict[str, Any], age_days: int | None) -> list[Any]:
        from app.recommendations.generators import blood_generator

        return blood_generator.generate(diego, _panel(values, age_days))

    def test_a_fresh_panel_advises_and_says_which_panel_it_read(self, diego: User) -> None:
        from app.recommendations.generators import blood_generator

        panel = _panel(self._ABNORMAL, age_days=10)
        candidates = blood_generator.generate(diego, panel)

        assert candidates, "un panel fresco con dos marcadores fuera de rango tiene que aconsejar"
        assert all(c["subject_type"] == "biomarker" for c in candidates)
        assert panel.analysis_date is not None
        iso = panel.analysis_date.isoformat()
        #: La fecha va en el texto y no solo en `evidence_summary`: el punto de la 4.5.6 es que
        #: la persona pueda ver de cuándo es el número antes de decidir si le sirve.
        assert all(iso in c["text"] for c in candidates)
        assert all(iso in c["rationale"] for c in candidates)

    #: Lo que decía el catálogo antes de la 4.5.6, palabra por palabra: `rationale` era
    #: *"Low hemoglobin may indicate iron deficiency anemia."* y el `text` un imperativo con
    #: la condición nombrada. Nombrar una condición a partir de un número es el paso que no
    #: le toca a la app, y es el que una entrada nueva reintroduce sin que nada más lo note.
    _DIAGNOSTIC_WORDS = (
        "anemia",
        "deficiency",
        "diabetes",
        "hypothyroid",
        "hyperthyroid",
        "disease",
        "disorder",
        "syndrome",
        "may indicate",
        "suggests",
        "diagnos",
    )

    def test_no_entry_in_the_catalog_names_a_condition(self) -> None:
        offenders = [
            f"{key}/{bucket}: {word}"
            for key, buckets in blood_generator._BIOMARKER_ADVICE.items()
            for bucket, entries in buckets.items()
            for advice in entries
            for word in self._DIAGNOSTIC_WORDS
            if word in f"{advice.title} {advice.action} {advice.mechanism}".lower()
        ]
        assert offenders == [], (
            "una entrada volvió al encuadre diagnóstico: la tarjeta observa un número y "
            f"propone comida o movimiento, no nombra una condición ({', '.join(offenders)})"
        )

    def test_the_screens_that_show_these_cards_carry_the_disclaimer(self) -> None:
        """La otra mitad del encuadre, y la que se puede borrar sin que falle nada más.

        La línea de no-diagnóstico no va dentro del `text` de cada tarjeta —quedaba tres veces
        en la misma pantalla y en inglés fijo, porque estas cadenas se persisten renderizadas—
        sino una vez por pantalla y traducida. Eso deja el encuadre dependiendo de dos
        plantillas, así que acá se mide que sigan llevándolo: sin este test, borrar el
        `ui.notice` deja el consejo de sangre sin ningún aviso y la suite en verde.
        """
        root = Path(__file__).resolve().parents[1]
        for relative in blood_generator._DISCLAIMER_TEMPLATES:
            source = (root / relative).read_text(encoding="utf-8")
            assert "not a diagnosis" in source, (
                f"{relative} dejó de llevar el aviso de no-diagnóstico, que es el único "
                "lugar donde vive: las tarjetas de sangre no lo repiten"
            )
            assert "blood_analysis" in source, (
                f"{relative} lleva el aviso pero ya no lo condiciona a que haya una tarjeta "
                "de sangre en la lista"
            )

    def test_a_stale_panel_advises_with_the_caveat_and_ranks_lower(self, diego: User) -> None:
        fresh = {c["title"]: c for c in self._generate(diego, self._ABNORMAL, 10)}
        stale = {c["title"]: c for c in self._generate(diego, self._ABNORMAL, 200)}

        #: Mismas tarjetas: pasada la frescura el consejo no cambia de contenido, cambia de
        #: peso y de encuadre. Si dejaran de coincidir, comparar las confianzas de abajo no
        #: mediría nada.
        assert set(fresh) == set(stale)
        for title, stale_card in stale.items():
            assert stale_card["confidence"] < fresh[title]["confidence"]
            assert "may no longer describe you" in stale_card["text"]
            assert str(blood_generator._FRESH_DAYS) in stale_card["rationale"]

    def test_an_obsolete_panel_advises_nothing_and_asks_for_a_new_one(self, diego: User) -> None:
        candidates = self._generate(diego, self._ABNORMAL, 500)

        assert len(candidates) == 1, "pasado el techo sale una sola tarjeta, no una por marcador"
        card = candidates[0]
        assert card["subject_type"] == "habit"
        assert card["subject_name"] == blood_generator._REFRESH_SUBJECT
        #: Cuántos marcadores quedaron sin leer, no cuáles: nombrarlos sería dar exactamente el
        #: consejo que esta rama existe para no dar.
        assert "2" in card["text"]
        assert not {"hemoglobin", "ldl"} & set(card["text"].lower().split())

    def test_an_undated_panel_gets_its_own_message_and_no_advice(self, diego: User) -> None:
        """Sin fecha cuenta como viejo, no como nuevo — y lo dice distinto.

        Son dos situaciones que se arreglan de maneras distintas: un panel de dos años se
        repite, uno cuya fecha no se pudo leer se vuelve a subir. Que compartan la banda
        haría que la tarjeta le pidiera un análisis nuevo a quien ya tiene uno reciente.
        """
        undated = self._generate(diego, self._ABNORMAL, None)
        obsolete = self._generate(diego, self._ABNORMAL, 500)

        assert len(undated) == 1
        assert undated[0]["subject_name"] == blood_generator._REFRESH_SUBJECT
        assert undated[0]["title"] != obsolete[0]["title"]
        assert "no date" in undated[0]["text"]

    def test_an_obsolete_panel_with_nothing_out_of_range_says_nothing(self, diego: User) -> None:
        """El aviso de repetir el panel tiene una razón, y sin la razón no hay aviso.

        La razón es que había algo sin leer. Con todos los marcadores en rango, pedir un
        análisis nuevo sería una nota al pie con forma de alarma.
        """
        assert self._generate(diego, self._ALL_NORMAL, 500) == []
        assert self._generate(diego, self._ALL_NORMAL, None) == []

    def test_the_referral_cards_stay_and_are_filtered_like_every_other_candidate(
        self, diego: User
    ) -> None:
        """Las derivaciones se quedan —decirle a alguien que consulte a quien pidió el análisis
        es lo correcto— pero atraviesan los filtros como todas. Son las tarjetas
        `category="habit"` que hasta la 4.5.5 los eludían por venir de una categoría que el
        filtro no sabía clasificar, así que acá se mide lo que quedó cerrado: que sigan
        saliendo, y que un bloqueo declarado alcance a la que le corresponde.
        """
        candidates = self._generate(
            diego,
            {
                "tsh": {"value": 6.1, "unit": "mUI/L", "status": "high"},
                "creatinine": {"value": 1.6, "unit": "mg/dL", "status": "high"},
            },
            age_days=10,
        )
        referrals = [c for c in candidates if c["category"] == "habit"]
        assert len(referrals) == 2, "las dos derivaciones tienen que seguir emitiéndose"

        #: Sin nada que bloquear no desaparece ninguna, ni por accidente: Diego tiene natación
        #: como imposible y ninguna de estas dos tarjetas la nombra.
        assert apply_hard_constraints(candidates, diego, []) == candidates

        #: Una restricción de líquidos es exactamente el caso en el que "tomá más agua" no se
        #: le puede decir a alguien, y es un bloqueo declarado como cualquier otro.
        no_water = RecommendationPreference(
            user_id=diego.id,
            item_type="food",
            item_name="water",
            preference_signal="avoid",
            strength=1.0,
        )
        kept = apply_hard_constraints(candidates, diego, [no_water])
        creatinine_card = next(c for c in referrals if c["subject_name"] == "creatinine")
        assert creatinine_card not in kept
        assert next(c for c in referrals if c["subject_name"] == "tsh") in kept
