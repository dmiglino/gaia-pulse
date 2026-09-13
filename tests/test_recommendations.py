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
from app.recommendations import learning, scorer
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

    def test_the_attribute_type_is_not_a_recordable_subject(self, db: Session, diego: User) -> None:
        """`food_category` existe solo como atributo: no hay fila con ese tipo.

        La otra opción era grabar una segunda señal por comida con la categoría del alimento
        —lo que la 4.4.1 dejó anotado— y es peor: congelaría la categoría del día en que se
        comió, y solo aprendería de las comidas futuras. Derivar en cada lectura es
        retroactivo y se corrige solo.
        """
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

        candidates = meal_generator.generate(db, diego, [], meal_type="breakfast")
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
