"""Que registrar la vida real enseñe, y que lo aprendido cambie lo que se sugiere.

La 4.4 arregló el *keying* del aprendizaje —se aprende sobre un sujeto y no sobre el
título renderizado—, pero el arreglo no sirve si nadie escribe. El síntoma que dio origen
a todo esto es `repeated_purchase`: el scorer lo leía como señal positiva y **ningún
código lo escribía nunca**, así que comprar leche todas las semanas no movía una sola
sugerencia. Este módulo cubre el otro lado del vocabulario: los tres escritores
implícitos —comida, entrenamiento, compra—, qué exactamente aprenden de cada registro, y
que lo que escriben efectivamente llega al scorer.

El último test es estructural y es el que impide que el bug vuelva: recorre las fuentes
buscando cada tipo de señal que el lector dice entender, y falla si alguno no aparece en
ningún escritor.
"""

from __future__ import annotations

import ast
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.jobs import suggestion_jobs
from app.models.food import FoodItem
from app.models.household import Household
from app.models.signal import BehaviorSignal
from app.models.suggestion import Suggestion
from app.models.user import User
from app.models.workout import ExerciseType
from app.recommendations import learning
from app.recommendations.scorer import score_candidates
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.schemas.pantry import PurchaseItem, PurchaseRequest
from app.schemas.suggestion import SuggestionFeedback
from app.schemas.workout import (
    WorkoutExerciseCreate,
    WorkoutParticipantCreate,
    WorkoutSessionCreate,
)
from app.services.learning_service import GROUP_ORDER, LearningService
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
from app.services.suggestion_service import SuggestionService
from app.services.workout_service import WorkoutService

APP_ROOT = Path(__file__).resolve().parent.parent / "app"


def _signals(db: Session, user: User) -> list[BehaviorSignal]:
    """Las señales de *user*, ordenadas por id para no depender del orden de filas."""
    return (
        db.query(BehaviorSignal)
        .filter(BehaviorSignal.user_id == user.id)
        .order_by(BehaviorSignal.id)
        .all()
    )


def _subjects(signals: list[BehaviorSignal]) -> set[tuple[str, str]]:
    return {(s.entity_type, s.entity_name) for s in signals}


def _log_meal(
    db: Session,
    household: Household,
    *,
    participants: list[MealParticipantCreate],
    meal_type: str = "lunch",
) -> Any:
    return MealService(db).log_meal(
        household.id,
        MealEventCreate(
            timestamp=datetime.now(UTC),
            meal_type=meal_type,
            participants=participants,
        ),
    )


class TestWhatAMealTeaches:
    def test_each_item_becomes_one_signal_pointing_back_at_the_meal(
        self, db: Session, household: Household, diego: User
    ) -> None:
        event = _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id,
                    items=[
                        MealItemCreate(food_name="Lentejas", quantity=1, unit="bowl"),
                        MealItemCreate(food_name="arroz", quantity=100, unit="g"),
                    ],
                )
            ],
        )

        signals = _signals(db, diego)
        assert len(signals) == 2
        assert {s.signal_type for s in signals} == {"repeated_meal_choice"}
        assert _subjects(signals) == {("food", "lentejas"), ("food", "arroz")}
        for signal in signals:
            assert float(signal.value) == 1.0
            #: Implícita: la persona no dijo "me gustan las lentejas", las comió. La
            #: distinción importa porque la 4.4.2 les va a dar vidas medias distintas.
            assert signal.source_type == "implicit"
            assert signal.source_entity_type == "meal_event"
            assert signal.source_entity_id == event.id

    def test_the_time_of_day_travels_with_the_signal(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Un café es un gusto *del desayuno*, y eso ya se sabía al registrarlo.

        `MealEvent.meal_type` se guardaba desde siempre y nadie lo leía para aprender.
        Sin esto la 4.4.5 —contexto horario— tendría que volver a buscar la comida por
        `source_entity_id` para averiguar algo que estaba a mano al escribir.
        """
        _log_meal(
            db,
            household,
            meal_type="breakfast",
            participants=[
                MealParticipantCreate(user_id=diego.id, items=[MealItemCreate(food_name="cafe")])
            ],
        )

        (signal,) = _signals(db, diego)
        assert signal.context_json == {"meal_type": "breakfast"}

    def test_a_shared_meal_teaches_each_person_only_their_own_plate(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Regla 4 de `AGENTS.md`: convivir no es compartir el historial.

        Comen juntos y cada uno se sirve otra cosa. Si el sujeto se atribuyera a la
        comida en vez de al participante, a Rocío le empezarían a llegar sugerencias de
        lo que come Diego, que es exactamente lo que el aprendizaje por persona evita.
        """
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id, items=[MealItemCreate(food_name="milanesa")]
                ),
                MealParticipantCreate(
                    user_id=rocio.id, items=[MealItemCreate(food_name="ensalada")]
                ),
            ],
        )

        assert _subjects(_signals(db, diego)) == {("food", "milanesa")}
        assert _subjects(_signals(db, rocio)) == {("food", "ensalada")}


class TestWhatAWorkoutTeaches:
    @staticmethod
    def _log(
        db: Session,
        household: Household,
        user: User,
        exercises: list[WorkoutExerciseCreate],
        workout_type: str | None = None,
    ) -> Any:
        return WorkoutService(db).log_workout(
            household.id,
            WorkoutSessionCreate(
                timestamp_start=datetime.now(UTC),
                workout_type=workout_type,
                participants=[WorkoutParticipantCreate(user_id=user.id, exercises=exercises)],
            ),
        )

    def test_the_exercise_and_its_muscle_group_are_both_learned(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Antes la única señal de un entrenamiento era su `workout_type`.

        Y `workout_type` es el *lugar* —"gym", "home", "outdoor"—, no la actividad: cien
        sesiones registradas no enseñaban nada sobre press de banca. El generador de
        actividad propone candidatos con sujeto `exercise` y `muscle_group`, así que
        estas dos filas sí las lee alguien.
        """
        session = self._log(
            db,
            household,
            diego,
            [
                WorkoutExerciseCreate(
                    exercise_name="Press de banca", muscle_group="chest", sets=4, reps=8
                )
            ],
        )

        signals = _signals(db, diego)
        assert _subjects(signals) == {
            ("exercise", "press de banca"),
            ("muscle_group", "chest"),
        }
        for signal in signals:
            assert signal.signal_type == "repeated_activity"
            assert float(signal.value) == 1.0
            assert signal.source_entity_type == "workout_session"
            assert signal.source_entity_id == session.id

    def test_the_place_is_still_learned_alongside_what_was_done(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """La señal de `workout_type` que ya existía no se reemplazó, se complementó."""
        self._log(
            db,
            household,
            diego,
            [WorkoutExerciseCreate(exercise_name="sentadillas", muscle_group="legs")],
            workout_type="gym",
        )

        assert _subjects(_signals(db, diego)) == {
            ("exercise", "sentadillas"),
            ("muscle_group", "legs"),
            ("exercise", "gym"),
        }

    def test_a_brutal_set_counts_for_half(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Un RPE de 9 o 10 dice "se hizo y costó", no "esto es lo que voy a elegir".

        Haber hecho algo es evidencia de que se puede repetir; llevarlo al límite es
        evidencia de lo contrario. Un RPE bajo **no** se castiga —una sesión liviana es
        perfectamente repetible—, así que la escala es de un solo lado.
        """
        self._log(
            db,
            household,
            diego,
            [
                WorkoutExerciseCreate(exercise_name="burpees", perceived_effort=10),
                WorkoutExerciseCreate(exercise_name="caminata", perceived_effort=3),
                WorkoutExerciseCreate(exercise_name="remo", perceived_effort=None),
            ],
        )

        by_subject = {s.entity_name: float(s.value) for s in _signals(db, diego)}
        assert by_subject == {"burpees": 0.5, "caminata": 1.0, "remo": 1.0}

    def test_an_exercise_without_a_muscle_group_does_not_get_one_invented(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Resolverlo contra `ExerciseType` es la 4.5; adivinarlo no es de nadie."""
        self._log(db, household, diego, [WorkoutExerciseCreate(exercise_name="bicicleta")])

        assert _subjects(_signals(db, diego)) == {("exercise", "bicicleta")}

    def test_each_participant_learns_only_their_own_exercises(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Entrenan en la misma sesión y hacen cosas distintas (regla 4)."""
        WorkoutService(db).log_workout(
            household.id,
            WorkoutSessionCreate(
                timestamp_start=datetime.now(UTC),
                participants=[
                    WorkoutParticipantCreate(
                        user_id=diego.id,
                        exercises=[WorkoutExerciseCreate(exercise_name="dominadas")],
                    ),
                    WorkoutParticipantCreate(
                        user_id=rocio.id,
                        exercises=[WorkoutExerciseCreate(exercise_name="yoga")],
                    ),
                ],
            ),
        )

        assert _subjects(_signals(db, diego)) == {("exercise", "dominadas")}
        assert _subjects(_signals(db, rocio)) == {("exercise", "yoga")}


class TestWhatAPurchaseTeaches:
    """El circuito que estaba abierto: `repeated_purchase` se leía y no se escribía."""

    @staticmethod
    def _purchase(db: Session, household: Household, user: User, *names: str) -> None:
        PantryService(db).process_purchase(
            household.id,
            user.id,
            PurchaseRequest(items=[PurchaseItem(food_name=name, quantity=1) for name in names]),
        )

    def test_a_purchase_teaches_the_food_at_half_the_weight_of_a_meal(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Comprar dice menos que comer: se compra para el otro, se compra y se tira.

        Y además se atribuye a quien registró la compra, que en una casa de dos es quien
        fue al súper y no necesariamente quien lo come. Es una pista, no una preferencia.
        """
        self._purchase(db, household, diego, "yerba")

        (signal,) = _signals(db, diego)
        assert signal.signal_type == "repeated_purchase"
        assert (signal.entity_type, signal.entity_name) == ("food", "yerba")
        assert float(signal.value) == 0.5
        assert signal.source_type == "implicit"

    def test_the_signal_points_back_at_the_movement_it_came_from(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Sin el `flush` previo el `movement.id` es `None` y la fila queda sin rastro.

        `_make_movement` solo hace `add`, así que la clave no existe hasta que alguien
        va a la base. Y de este puntero depende poder explicar la señal y poder
        olvidarla, que es la 4.4.8.
        """
        self._purchase(db, household, diego, "leche", "huevos")

        signals = _signals(db, diego)
        assert len(signals) == 2
        assert all(s.source_entity_type == "pantry_movement" for s in signals)
        ids = [s.source_entity_id for s in signals]
        assert all(i is not None for i in ids)
        #: Cada ítem apunta a *su* movimiento: un solo flush al final del bucle hacía que
        #: los dos terminaran con el id del último.
        assert len(set(ids)) == 2

    def test_the_accent_does_not_split_the_subject_in_two(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El catálogo escribe "brócoli"; el texto libre de una captura, "brocoli".

        `PurchaseItem` ya baja a minúsculas, pero no saca tildes: sin la normalización de
        `learning` la compra y la comida quedaban como dos sujetos que nunca se sumaban.
        """
        self._purchase(db, household, diego, "Brócoli")

        (signal,) = _signals(db, diego)
        assert signal.entity_name == "brocoli"
        assert signal.entity_name == learning.normalize_subject("brocoli")


def _backdate(db: Session, signals: list[BehaviorSignal], *, days: float) -> None:
    """Corre *signals* al pasado, porque una fila recién escrita no es solo un gusto.

    Hace falta desde la 4.4.6: la misma fila que dice "esto le gusta" dice también "esto lo
    consumió hace tanto", y la saciedad la lee con un reloj catorce veces más corto. Una
    señal escrita en esta transacción es, para ese lector, comida que está pasando ahora
    mismo — el caso de saciedad máxima. Estos tests hablan del gusto, así que el acto tiene
    que estar donde está siempre en la vida real: en el pasado. La sugerencia se calcula
    cuando corre el job, no en la transacción que registra la cena.
    """
    when = datetime.now(UTC) - timedelta(days=days)
    for signal in signals:
        signal.created_at = when
    db.flush()


def _card(db: Session, user: User, *, days_old: float = 0.0, **overrides: Any) -> Suggestion:
    """Una sugerencia pendiente de *user*, opcionalmente nacida hace *days_old* días.

    Estaba escrita dos veces —una en la clase que prueba las respuestas con motivo y otra en
    la del barrido—, con los mismos once campos y la misma diferencia de una línea. Dos
    fábricas de la misma fila es cómo un test empieza a probar una tarjeta que el resto del
    módulo ya no construye así: `Suggestion` tiene columnas obligatorias, y la que se agregue
    hay que agregarla en los dos lados o uno de los dos deja de arrancar.

    El envejecido va **después** del `flush` porque `created_at` tiene default en la base: si
    se pasara al constructor, el default lo pisa y la tarjeta nace nueva sin que nada avise.
    """
    defaults: dict[str, Any] = {
        "scope_type": "user",
        "household_id": user.household_id,
        "scope_user_id": user.id,
        "category": "meal",
        "subject_type": "food",
        "subject_name": "acelga",
        "title": "Cená algo verde",
        "text": "Una acelga saltada.",
        "rationale": "Variedad.",
        "source_type": "rule",
        "status": "pending",
    }
    defaults.update(overrides)
    card = Suggestion(**defaults)
    db.add(card)
    db.flush()
    if days_old:
        card.created_at = datetime.now(UTC) - timedelta(days=days_old)
        db.flush()
    return card


class TestWhatIsLearnedChangesWhatIsSuggested:
    """El pago de todo lo anterior: si esto no pasa, escribir señales es decoración."""

    def test_eating_and_buying_something_pushes_it_up_the_ranking(
        self, db: Session, household: Household, diego: User
    ) -> None:
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id, items=[MealItemCreate(food_name="lentejas")]
                )
            ],
        )
        PantryService(db).process_purchase(
            household.id,
            diego.id,
            PurchaseRequest(items=[PurchaseItem(food_name="lentejas", quantity=1)]),
        )
        _backdate(db, _signals(db, diego), days=7)

        candidates = [
            {
                "title": "Sopa de zapallo",
                "confidence": 0.5,
                "subject_type": "food",
                "subject_name": "zapallo",
            },
            {
                "title": "Guiso de lentejas",
                "confidence": 0.5,
                "subject_type": "food",
                "subject_name": "Lentejas",
            },
        ]
        scored = score_candidates(candidates, diego, _signals(db, diego), [])

        assert scored[0]["title"] == "Guiso de lentejas"
        assert scored[0]["_score"] > 0.5
        #: El sujeto que nadie tocó se queda con su confianza cruda: el boost es del
        #: sujeto aprendido, no de la lista.
        assert scored[1]["_score"] == 0.5

    def test_a_purchase_alone_already_moves_the_score(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """Concretamente el bug que originó la 4.4: esto antes no movía nada."""
        PantryService(db).process_purchase(
            household.id,
            diego.id,
            PurchaseRequest(items=[PurchaseItem(food_name="quinoa", quantity=1)]),
        )
        _backdate(db, _signals(db, diego), days=7)

        candidate = {
            "title": "Ensalada de quinoa",
            "confidence": 0.5,
            "subject_type": "food",
            "subject_name": "quinoa",
        }
        (scored,) = score_candidates([candidate], diego, _signals(db, diego), [])
        assert scored["_score"] > 0.5

    def test_an_old_meal_barely_counts_next_to_a_recent_one(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """ "Comí esto una vez en 2024" no puede pesar lo mismo que "lo comí ayer".

        Antes de la 4.4.2 pesaba **exactamente** lo mismo y el corte era una ventana de 30
        días, así que el aprendizaje era un promedio de la historia con un borde duro. Ahora
        la señal vieja sigue entrando —la consulta llega hasta el horizonte— y lo que la
        vuelve irrelevante es su semivida.
        """
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id,
                    items=[
                        MealItemCreate(food_name="ravioles"),
                        MealItemCreate(food_name="ñoquis"),
                    ],
                )
            ],
        )
        signals = sorted(_signals(db, diego), key=lambda s: s.entity_name)
        old, recent = signals
        assert (old.entity_name, recent.entity_name) == ("noquis", "ravioles")
        #: "Reciente" es una semana, no este segundo: lo que se compara acá son dos edades,
        #: y una comida de hace un rato traería además su propia saciedad.
        _backdate(db, signals, days=7)
        old.created_at = datetime.now(UTC) - timedelta(days=200)
        db.flush()

        candidates = [
            {
                "title": "Ñoquis del 29",
                "confidence": 0.5,
                "subject_type": "food",
                "subject_name": "ñoquis",
            },
            {
                "title": "Ravioles caseros",
                "confidence": 0.5,
                "subject_type": "food",
                "subject_name": "ravioles",
            },
        ]
        scored = score_candidates(candidates, diego, _signals(db, diego), [])

        assert scored[0]["title"] == "Ravioles caseros"
        #: Doscientos días son casi diez semividas de una señal implícita: lo que queda es
        #: ruido a nivel del redondeo del score, no una preferencia.
        assert scored[1]["_score"] < 0.501


class TestReadingTheReasonSomeoneWrote:
    """El matcher de la 4.4.7: qué sujeto sale de una frase escrita a mano.

    Es una función pura y por eso se prueba sin base: recibe el texto y el vocabulario, y
    la única decisión que toma es contra qué nombre conocido coincide. Lo que estos tests
    fijan es que **no invente**: el motivo es texto libre y de una lectura equivocada sale
    una preferencia que la persona nunca declaró.
    """

    @staticmethod
    def _vocab(*names: str) -> dict[str, str]:
        """El vocabulario de un catálogo donde cada nombre es su propio canónico.

        La forma real la arma `FoodRepository.known_names`, que mapea cada alias al canónico
        de su alimento; para los tests que no son sobre alias, cada nombre se mapea a sí
        mismo, que es lo que hace un catálogo sin alias.
        """
        return {name: name for name in names}

    def test_the_named_food_is_what_comes_out_and_not_the_wording(self) -> None:
        assert learning.subjects_in_text(
            "no nos gusta el brócoli", foods=self._vocab("brócoli", "queso", "lentejas")
        ) == [("food", "brocoli")]

    def test_an_alias_teaches_about_the_canonical_name_and_not_about_itself(self) -> None:
        """El caso normal de esta casa, y el que se aprendía sin poder leerse.

        El catálogo se escribe con el canónico en inglés y el castellano como alias
        —`FoodItem.aliases_json` documenta `["tomato", "tomate"]`— y los candidatos declaran
        su sujeto con `canonical_name`. Grabar el alias que matcheó dejaba la señal de "palta"
        esperando un candidato llamado "palta" que ningún generador escribe nunca.
        """
        assert learning.subjects_in_text(
            "no nos gusta la palta", foods={"avocado": "avocado", "palta": "avocado"}
        ) == [("food", "avocado")]

    def test_two_names_of_the_same_food_are_one_subject(self) -> None:
        """Decir "ni palta ni aguacate" es una opinión sobre un alimento, no sobre dos.

        Sin el dedup, la misma frase escribía dos señales del mismo peso sobre el mismo
        sujeto y el aprendizaje leía dos observaciones donde hubo una.
        """
        assert learning.subjects_in_text(
            "ni palta ni aguacate",
            foods={"avocado": "avocado", "palta": "avocado", "aguacate": "avocado"},
        ) == [("food", "avocado")]

    def test_a_longer_name_swallows_the_shorter_one_inside_it(self) -> None:
        """ "Queso crema" no puede enseñar además que no gusta el queso.

        El match consume lo que encontró; sin eso, quien escribe el nombre más específico
        que el catálogo conoce se lleva de regalo un rechazo del genérico, que es una
        opinión más amplia que la que dio.
        """
        assert learning.subjects_in_text(
            "el queso crema no nos va", foods=self._vocab("queso", "queso crema")
        ) == [("food", "queso crema")]

    def test_a_name_the_catalogue_does_not_know_teaches_nothing(self) -> None:
        """El vocabulario es cerrado, y esa es la diferencia con `_parse_preference`.

        Un matcher abierto —"quedate con las palabras que siguen a la negación"— convierte
        cualquier frase en un sujeto: de *"no, gracias"* saldría un alimento llamado
        "gracias", con su señal, su decaimiento y su lugar en el panel de lo aprendido.
        """
        assert learning.subjects_in_text("no, gracias", foods=self._vocab("brócoli")) == []

    def test_a_name_hidden_inside_a_longer_word_is_not_a_match(self) -> None:
        """El match es de frase entera: "té" no está en "tenemos".

        Sin el padding de espacios, los nombres cortos del catálogo —"té", "ajo", "sal"—
        coincidirían dentro de media docena de palabras corrientes y toda la casa
        terminaría con preferencias sobre el té por haber escrito "no tenemos tiempo".
        """
        assert learning.subjects_in_text("no tenemos tiempo", foods=self._vocab("té")) == []

    def test_an_activity_is_found_where_the_old_parser_would_have_invented_one(
        self,
    ) -> None:
        """La razón por la que el matcher de reglas se reutiliza y `_parse_preference` no.

        `_parse_preference` saca la negación y se queda con las tres primeras palabras, así
        que de esta frase deduciría algo llamado "para nosotros". El matcher exacto encuentra
        lo que la frase realmente nombra, y no encuentra nada cuando no nombra nada.
        """
        from app.nlp import rules

        assert rules.find_known_activities("no es para nosotros, el yoga nos aburre") == ["yoga"]
        assert learning.subjects_in_text("no es para nosotros, el yoga nos aburre", foods={}) == [
            ("exercise", "yoga")
        ]

    def test_an_activity_alias_teaches_about_the_catalogue_name_and_not_about_itself(
        self,
    ) -> None:
        """La 7.5: el mismo caso de la palta, del lado de los ejercicios.

        El catálogo se siembra con el canónico en inglés y el castellano como alias
        (`ExerciseType.aliases_json`), y los candidatos declaran su sujeto con
        `ExerciseType.name`. Grabar "press de banca" tal cual dejaba la señal esperando un
        candidato que ningún generador escribe.
        """
        assert learning.subjects_in_text(
            "no nos gusta el press de banca",
            foods={},
            activities={"Bench Press": "Bench Press", "press de banca": "Bench Press"},
        ) == [("exercise", "bench press")]

    def test_the_catalogue_alias_and_the_rules_map_both_contribute(self) -> None:
        """Los dos vocabularios cerrados de la 7.5 se combinan y no se pisan.

        "press de banca" solo lo conoce el catálogo (vía alias); "yoga" solo lo conoce
        `rules._EXERCISE_MAP`. Ninguno de los dos tapa al otro.
        """
        assert set(
            learning.subjects_in_text(
                "el press de banca no, y el yoga tampoco",
                foods={},
                activities={"press de banca": "Bench Press"},
            )
        ) == {("exercise", "bench press"), ("exercise", "yoga")}

    def test_a_food_and_an_activity_in_the_same_sentence_are_both_learned(self) -> None:
        assert set(
            learning.subjects_in_text(
                "el brócoli no, y el running tampoco", foods=self._vocab("brócoli")
            )
        ) == {("food", "brocoli"), ("exercise", "running")}

    def test_an_empty_reason_is_not_a_subject(self) -> None:
        assert learning.subjects_in_text("   ", foods=self._vocab("brócoli")) == []

    def test_the_vocabulary_includes_the_foods_the_house_invented(
        self, db: Session, banana: FoodItem
    ) -> None:
        """Un alimento sin categoría entra al vocabulario del matcher.

        Es la única diferencia con `attribute_index`, y es deliberada: los alimentos sin
        categoría son justamente los que creó `get_or_create` con lo que la casa escribió en
        una captura. Dejarlos afuera haría que la app no reconozca los nombres que la propia
        persona usa. Para generalizar la categoría hace falta; para leer "brócoli" en una
        frase, no.
        """
        db.add(FoodItem(canonical_name="brócoli", base_unit="g", aliases_json=["brocoli"]))
        db.flush()

        vocabulary = learning.food_vocabulary(db)
        assert vocabulary["brócoli"] == "brócoli"
        #: El alias también entra, y apunta al canónico: es lo que hace que la señal minada
        #: de una frase en castellano se encuentre con el candidato que el generador declara.
        assert vocabulary["brocoli"] == "brócoli"
        assert banana.canonical_name in vocabulary

    def test_the_activity_vocabulary_resolves_from_both_ends(self, db: Session) -> None:
        """La contraparte de comida (7.5): el alias en castellano apunta al canónico.

        Antes de la `0004` este vocabulario no existía —el único lado de actividades era
        `rules._EXERCISE_MAP`, en inglés— y `learning.activity_vocabulary` es lo que hace
        que "press de banca" resuelva contra "Bench Press" igual que "palta" resuelve
        contra "avocado".
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

        vocabulary = learning.activity_vocabulary(db)
        assert vocabulary["Bench Press"] == "Bench Press"
        assert vocabulary["press de banca"] == "Bench Press"


class TestWhatTheReasonTeaches:
    """El otro extremo: la respuesta a una sugerencia con un motivo escrito.

    Hasta la 4.4.7 el motivo se guardaba en `feedback_notes` y ahí terminaba, así que
    *"no me gusta el brócoli"* sobre una tarjeta titulada "Cená algo verde" enseñaba que
    no gustan las cosas verdes. Estos tests son sobre el camino completo: el texto entra
    por el servicio y sale como señales con el sujeto correcto.
    """

    @staticmethod
    def _respond(db: Session, diego: User, card: Suggestion, status: str, reason: str) -> None:
        SuggestionService(db).respond_to_suggestion(
            card.id,
            SuggestionFeedback(status=status, feedback_notes=reason),
            diego.id,
            diego.household_id,
        )

    def test_the_reason_names_the_subject_and_the_title_does_not(
        self, db: Session, diego: User
    ) -> None:
        db.add(FoodItem(canonical_name="brócoli", base_unit="g"))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "rejected", "no nos gusta el brócoli")

        learned = {(s.entity_type, s.entity_name): s for s in _signals(db, diego)}
        #: Lo de la tarjeta se sigue grabando —la persona sí dijo no a *esta* tarjeta—, y
        #: además ahora se graba de qué habló.
        assert ("food", "acelga") in learned
        brocoli = learned[("food", "brocoli")]
        assert brocoli.signal_type == "explicit_preference"
        assert float(brocoli.value) == -1.0
        #: Con `source_type="explicit"`, o sea 90 días de semivida: lo dijo con palabras.
        assert brocoli.source_type == "explicit"
        #: Queda de dónde salió la señal, no una copia de la frase: el motivo ya está una vez
        #: en `suggestions.feedback_notes` y `source_entity_id` apunta ahí. Copiarlo en cada
        #: señal minada multiplicaba la misma oración personal en una tabla que nadie poda.
        assert brocoli.context_json == {"mined_from": "feedback_notes"}
        assert brocoli.source_entity_type == "suggestion"
        assert brocoli.source_entity_id == card.id

    def test_naming_the_cards_own_subject_does_not_count_it_twice(
        self, db: Session, diego: User
    ) -> None:
        db.add(FoodItem(canonical_name="acelga", base_unit="g"))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "rejected", "la acelga no")

        acelga = [s for s in _signals(db, diego) if s.entity_name == "acelga"]
        assert len(acelga) == 1
        assert acelga[0].signal_type == "rejected_suggestion"

    def test_a_soft_no_mines_softly(self, db: Session, diego: User) -> None:
        """El peso sale de la respuesta y no de una perilla nueva.

        Un "ahora no" que nombra el brócoli es un "ahora no" al brócoli: −0.3, lo mismo que
        graba el botón. Que el motivo pese siempre −1.0 haría que escribir por qué sea más
        duro que rechazar, lo cual castiga justamente a quien se tomó el trabajo de explicar.
        """
        db.add(FoodItem(canonical_name="brócoli", base_unit="g"))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "dismissed", "hoy no, el brócoli nos empachó")

        (brocoli,) = [s for s in _signals(db, diego) if s.entity_name == "brocoli"]
        assert float(brocoli.value) == -0.3

    def test_a_later_is_not_an_opinion_about_what_it_names(self, db: Session, diego: User) -> None:
        """`snoozed` no mina, y está escrito que es a propósito.

        "Más tarde" habla del momento, no de la cosa. Un motivo al lado de un "más tarde"
        —"hoy no, comimos brócoli al mediodía"— explica la demora; leerlo como un veto
        inventaría una opinión que nadie dio. La supresión de ese gesto vive en
        `snoozed_until`, no en una señal.
        """
        db.add(FoodItem(canonical_name="brócoli", base_unit="g"))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "snoozed", "hoy no, comimos brócoli al mediodía")

        assert _signals(db, diego) == []

    def test_a_card_without_a_subject_still_learns_from_the_words(
        self, db: Session, diego: User
    ) -> None:
        """Las filas anteriores a la `0003` no tienen sujeto, pero el motivo sí.

        Por eso el minado se lee antes del chequeo de sujeto y no después: al final del
        método, responder una de esas filas con un motivo escrito no enseñaba nada de nada.
        """
        db.add(FoodItem(canonical_name="brócoli", base_unit="g"))
        db.flush()
        card = _card(db, diego, subject_type=None, subject_name=None)

        self._respond(db, diego, card, "rejected", "no nos gusta el brócoli")

        (brocoli,) = _signals(db, diego)
        assert (brocoli.entity_type, brocoli.entity_name) == ("food", "brocoli")

    def test_a_mined_no_lowers_the_score_but_never_vetoes(self, db: Session, diego: User) -> None:
        """La decisión central: una lectura de texto libre ordena, no prohíbe.

        `learning.rejected_subjects` solo mira `rejected_suggestion`, así que esto sale
        gratis — y el test existe para que siga saliendo gratis. El costo de equivocarse
        ordenando es que algo salga tercero; el de equivocarse filtrando es que no salga
        nunca y nadie entienda por qué.
        """
        from app.recommendations.filters import apply_signal_constraints

        db.add(FoodItem(canonical_name="brócoli", base_unit="g"))
        db.flush()
        card = _card(db, diego)
        self._respond(db, diego, card, "rejected", "no nos gusta el brócoli")

        signals = _signals(db, diego)
        candidate = {
            "title": "Brócoli al horno",
            "confidence": 0.5,
            "category": "meal",
            "subject_type": "food",
            "subject_name": "brócoli",
        }

        assert apply_signal_constraints([candidate], signals) == [candidate]
        (scored,) = score_candidates([candidate], diego, signals, [])
        assert scored["_score"] < 0.5

    def test_a_reason_in_spanish_reaches_a_candidate_named_in_english(
        self, db: Session, diego: User
    ) -> None:
        """El camino completo del caso que se aprendía sin poder leerse nunca.

        El catálogo se escribe con el canónico en inglés y el castellano como alias, y los
        candidatos declaran su sujeto con `canonical_name`. Mientras la señal se grababa con
        el alias que matcheó, escribir *"no nos gusta la palta"* dejaba una fila sobre "palta"
        y el candidato "avocado" seguía saliendo igual de arriba: aprendido y no leído.
        """
        db.add(FoodItem(canonical_name="avocado", base_unit="g", aliases_json=["palta"]))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "rejected", "no nos gusta la palta")

        signals = _signals(db, diego)
        (mined,) = [s for s in signals if s.signal_type == "explicit_preference"]
        assert (mined.entity_type, mined.entity_name) == ("food", "avocado")

        candidate = {
            "title": "Tostadas con palta",
            "confidence": 0.5,
            "category": "meal",
            "subject_type": "food",
            "subject_name": "avocado",
        }
        (scored,) = score_candidates([candidate], diego, signals, [])
        assert scored["_score"] < 0.5

    def test_a_reason_in_spanish_reaches_an_exercise_candidate_named_in_english(
        self, db: Session, diego: User
    ) -> None:
        """El camino completo del lado de los ejercicios (7.5), espejo del de comida.

        Antes de la `0004` no había alias que resolver: "press de banca" no era nadie
        para el índice y el candidato "Bench Press" seguía saliendo igual de arriba.
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
        card = _card(db, diego)

        self._respond(db, diego, card, "rejected", "no nos gusta el press de banca")

        signals = _signals(db, diego)
        (mined,) = [s for s in signals if s.signal_type == "explicit_preference"]
        assert (mined.entity_type, mined.entity_name) == ("exercise", "bench press")

        candidate = {
            "title": "Rutina de pecho",
            "confidence": 0.5,
            "category": "workout",
            "subject_type": "exercise",
            "subject_name": "Bench Press",
        }
        (scored,) = score_candidates([candidate], diego, signals, [])
        assert scored["_score"] < 0.5

    def test_a_reason_that_names_everything_teaches_at_most_five_things(
        self, db: Session, diego: User
    ) -> None:
        """El tope de sujetos por motivo, que no está por desconfianza sino por costo.

        El largo del texto no acota el trabajo: 500 caracteres alcanzan para nombrar decenas
        de alimentos del catálogo, y sin tope una sola respuesta escribía decenas de filas en
        `behavior_signals` —una tabla sin poda— repetible a la velocidad de un POST. El test
        no fija *cuáles* cinco quedan: el orden del matcher es por nombre más largo primero,
        que es un detalle de cómo se evita que "queso crema" grabe "queso", no una promesa.
        """
        catalogo = [
            "brócoli",
            "coliflor",
            "berenjena",
            "zapallo",
            "remolacha",
            "espinaca",
            "zanahoria",
            "morrón",
        ]
        for name in catalogo:
            db.add(FoodItem(canonical_name=name, base_unit="g"))
        db.flush()
        card = _card(db, diego)

        self._respond(db, diego, card, "rejected", "no queremos " + ", ".join(catalogo))

        signals = _signals(db, diego)
        mined = [s for s in signals if s.signal_type == "explicit_preference"]
        assert len(mined) == SuggestionService._MAX_MINED_SUBJECTS
        #: Y la respuesta en sí sigue enseñando sobre su propio sujeto: el tope acota lo
        #: minado, no lo que la persona respondió.
        own = [s.entity_name for s in signals if s.signal_type == "rejected_suggestion"]
        assert own == ["acelga"]


class TestAbsenceSweep:
    """El único negativo que nadie apretó: la sugerencia que pasó su semana sin usarse.

    Es la señal más fácil de escribir mal, porque afirma algo que no pasó. Estos tests
    fijan las cuatro condiciones que la hacen honesta: que espere la semana de gracia, que
    no vuelva a contar la misma tarjeta en la corrida siguiente, que se calle si la persona
    hizo la cosa igual, y que pese lo suficientemente poco como para no vetar sola.
    """

    #: Una edad **adentro** de la ventana del barrido, que desde la 4.4.10 tiene dos bordes:
    #: `(corte - _SWEEP_WINDOW_DAYS, corte]`. `GRACE + 1` —lo que decía acá— es exactamente
    #: el borde de abajo, y queda afuera por microsegundos: el job calcula su corte después
    #: de que el test creó la tarjeta, así que su `created_after` es un instante *posterior* a
    #: `created_at`. Con la edad en el medio de la ventana el test dice lo que quiere decir
    #: ("una tarjeta que le toca a este barrido") y no depende de qué lado del `>` cae un
    #: empate. Los dos bordes tienen su propio test más abajo.
    _SWEPT_AGE = learning.ABSENCE_GRACE_DAYS + suggestion_jobs._SWEEP_WINDOW_DAYS / 2

    @pytest.fixture(autouse=True)
    def _job_uses_the_test_session(self, monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
        """El job abre y cierra su propia sesión; acá tiene que usar la del test.

        Mismo arreglo que `tests/test_notification_jobs.py`: sin pisar `close` el `finally`
        del job cerraría la sesión que el test todavía necesita para leer lo que se
        escribió.
        """
        monkeypatch.setattr(suggestion_jobs, "SessionLocal", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)

    @staticmethod
    def _card(db: Session, user: User, *, days_old: float, **overrides: Any) -> Suggestion:
        """La tarjeta de `_card`, pero de lentejas: el sujeto del que hablan estos tests.

        Lo único que agrega es el sujeto, porque los tests que registran "lo hizo igual" lo
        registran con este nombre y el nombre tiene que ser el mismo en los dos lados. Los
        once campos de la fila los pone la fábrica del módulo.
        """
        return _card(
            db,
            user,
            days_old=days_old,
            **{
                "subject_name": "lentejas",
                "title": "Probá algo con lentejas",
                "text": "Un guiso.",
                **overrides,
            },
        )

    @staticmethod
    def _absences(db: Session, user: User) -> list[BehaviorSignal]:
        return [s for s in _signals(db, user) if s.signal_type == learning.ABSENCE_SIGNAL_TYPE]

    def test_a_card_younger_than_the_grace_period_teaches_nothing(
        self, db: Session, diego: User
    ) -> None:
        """Antes de la semana, "no lo comió" solo significa "todavía no le tocó"."""
        self._card(db, diego, days_old=learning.ABSENCE_GRACE_DAYS - 1)

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_a_card_that_aged_out_becomes_a_signal_pointing_back_at_it(
        self, db: Session, diego: User
    ) -> None:
        card = self._card(db, diego, days_old=self._SWEPT_AGE)

        suggestion_jobs.run_absence_sweep()

        absences = self._absences(db, diego)
        assert len(absences) == 1
        signal = absences[0]
        assert (signal.entity_type, signal.entity_name) == ("food", "lentejas")
        assert float(signal.value) == learning.ABSENCE_VALUE
        #: El puntero de vuelta no es decorativo: es lo que hace que la segunda corrida
        #: sepa que esta tarjeta ya se contó.
        assert (signal.source_entity_type, signal.source_entity_id) == ("suggestion", card.id)
        #: Y `"inferred"`, que es la semivida más corta: nadie dijo nada y nadie hizo nada.
        assert signal.source_type == "inferred"

    def test_a_failed_commit_does_not_leak_the_subject_name_into_the_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
        db: Session,
        diego: User,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """`IntegrityError.__str__()` carries its bound INSERT parameters.

        `_sweep_user` commits once, after writing the signal whose `entity_name` is the
        subject — "lentejas" here. If that commit fails, `logger.exception` on the raw
        exception would put "lentejas" straight in the log via the exception's own
        `__str__()`. `log_job_error` is what stands between the two: the job survives (its
        own per-user `try` catches it) and the name never reaches the log.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE)

        def _failing_commit() -> None:
            raise IntegrityError(
                "INSERT INTO behavior_signals (entity_name) VALUES (?)",
                ["lentejas"],
                Exception("UNIQUE constraint failed"),
            )

        monkeypatch.setattr(db, "commit", _failing_commit)

        with caplog.at_level(logging.ERROR):
            suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []
        assert "lentejas" not in caplog.text
        assert any("IntegrityError" in record.getMessage() for record in caplog.records)

    def test_running_twice_the_same_day_does_not_count_the_same_card_twice(
        self, db: Session, diego: User
    ) -> None:
        """Dos corridas el mismo día caen las dos adentro de la ventana. Igual cuenta una.

        Este es el caso que la ventana **no** puede cubrir —los dos barridos ven la misma
        tarjeta con la misma edad—, y el que sostiene el conjunto `swept` del job. Sin él, un
        reinicio del proceso o un barrido manual duplicaría la observación.

        Que el barrido de *mañana* no la vuelva a contar es otra cosa y lo prueba
        `test_forgetting_a_subject_survives_the_sweeps_that_come_after`: ahí lo que la frena
        es la ventana, y tiene que ser la ventana, porque el conjunto `swept` se puede borrar
        desde el panel.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE)

        suggestion_jobs.run_absence_sweep()
        suggestion_jobs.run_absence_sweep()

        assert len(self._absences(db, diego)) == 1

    def test_a_card_older_than_the_window_already_had_its_turn(
        self, db: Session, diego: User
    ) -> None:
        """El borde de arriba: una tarjeta que cumplió su semana hace un mes no se barre.

        Nada saca una sugerencia de `pending`, así que sin este borde la elegibilidad no
        terminaba nunca: cada tarjeta vieja seguía apareciendo en la consulta de todos los
        barridos futuros, y lo único que evitaba escribirla de nuevo era encontrar su propia
        señal. Con el borde, "ya le tocó" es una propiedad de la fecha y no de lo que quedó
        guardado.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE + 30)

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_forgetting_a_subject_survives_the_sweeps_that_come_after(
        self, db: Session, diego: User
    ) -> None:
        """Lo que la persona pidió borrar no puede volver solo a la mañana siguiente.

        Es el bug que cerró el borde de arriba de la ventana. La memoria de "esta tarjeta ya
        se contó" son las señales mismas, así que borrarlas —justo lo que hace el botón de
        olvidar del panel— devolvía la tarjeta al estado elegible, y el barrido del día
        siguiente reescribía la ausencia sin que hubiera pasado nada nuevo. Un dato personal
        que se pidió borrar y reaparece solo es peor que no haber tenido el botón.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE)
        suggestion_jobs.run_absence_sweep()
        assert len(self._absences(db, diego)) == 1

        LearningService(db).forget(diego.id, "food", "lentejas")
        assert self._absences(db, diego) == []

        #: El día siguiente sin mover el reloj: envejecer la tarjeta un intervalo de job es
        #: lo mismo que correr el barrido un día después, y es lo que la saca de la ventana.
        (card,) = db.query(Suggestion).all()
        card.created_at = datetime.now(UTC) - timedelta(
            days=self._SWEPT_AGE + suggestion_jobs._SWEEP_WINDOW_DAYS
        )
        db.flush()

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_doing_it_anyway_after_the_card_appeared_is_not_an_absence(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """Comer lentejas al día siguiente de la tarjeta es lo contrario de una ausencia.

        Y cuenta desde que la tarjeta nació, no desde el corte: que no se hayan vuelto a
        comer en los seis días siguientes no borra que se comieron.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE)
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id, items=[MealItemCreate(food_name="lentejas")]
                )
            ],
        )
        _backdate(db, _signals(db, diego), days=learning.ABSENCE_GRACE_DAYS - 1)

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_an_act_from_before_the_card_does_not_excuse_it(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """El espejo del test anterior, y la razón de que la comparación sea por fecha.

        Haber comido lentejas el mes pasado es justamente por qué el motor las sugirió. Si
        cualquier acto viejo alcanzara para cancelar la ausencia, el sujeto que más se
        sugiere sería el que nunca podría enseñar que no se está usando.
        """
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id, items=[MealItemCreate(food_name="lentejas")]
                )
            ],
        )
        _backdate(db, _signals(db, diego), days=30)
        self._card(db, diego, days_old=self._SWEPT_AGE)

        suggestion_jobs.run_absence_sweep()

        assert len(self._absences(db, diego)) == 1

    def test_a_card_that_was_already_answered_is_not_swept(self, db: Session, diego: User) -> None:
        """Solo las pendientes. Una tarjeta respondida ya enseñó lo que tenía que enseñar,
        y contarla otra vez como ausencia sumaría un segundo negativo por el mismo hecho.
        """
        self._card(db, diego, days_old=self._SWEPT_AGE, status="rejected")

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_a_household_card_has_nobody_to_learn_from(self, db: Session, diego: User) -> None:
        """Una sugerencia del hogar (`scope_user_id IS NULL`) la vieron los dos.

        Que nadie la haya usado no dice cuál de los dos no la usó, y la regla 4 de
        `AGENTS.md` pide que toda señal se grabe contra una persona. Así que no se barre.
        """
        self._card(
            db,
            diego,
            days_old=self._SWEPT_AGE,
            scope_type="household",
            scope_user_id=None,
        )

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_only_subjects_whose_absence_can_be_observed_are_swept(
        self, db: Session, diego: User
    ) -> None:
        """Un hábito no tiene escritor de acto, así que su ausencia no es un dato.

        Barrerlo igual escribiría un negativo permanente contra "constancia" o "descanso"
        por el solo hecho de que el esquema no registra esas cosas.
        """
        self._card(
            db,
            diego,
            days_old=self._SWEPT_AGE,
            category="habit",
            subject_type="habit",
            subject_name="constancia",
        )

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, diego) == []

    def test_the_card_says_whose_absence_it_is_and_the_household_does_not(
        self, db: Session, diego: User, rocio: User
    ) -> None:
        """La tarjeta de Rocío no enseña nada sobre Diego, aunque compartan la casa.

        Un job no tiene usuario que apriete el botón: el `user_id` de la señal no puede salir
        de la sesión, así que sale de `scope_user_id` de la tarjeta y de ningún otro lado
        —regla 4 de `AGENTS.md`, que pide `user_id` incluso entre dos personas del mismo
        hogar—. Con un `household_id` ahí, la comida que Rocío no comió le bajaría el puntaje
        a Diego y él lo vería en su propio panel de "lo que GaiaPulse dedujo".
        """
        self._card(db, rocio, days_old=self._SWEPT_AGE)

        suggestion_jobs.run_absence_sweep()

        assert len(self._absences(db, rocio)) == 1
        assert self._absences(db, diego) == []

    def test_a_deactivated_person_is_not_swept(self, db: Session, diego: User, rocio: User) -> None:
        """Una cuenta desactivada deja de aprender, igual que deja de recibir avisos.

        El barrido recorre `list_active()` como los otros siete jobs. Si recorriera a todos,
        una cuenta dada de baja seguiría acumulando negativos en silencio, y el día que se
        reactivara arrancaría con meses de "no usó nada" encima.
        """
        rocio.is_active = False
        self._card(db, rocio, days_old=self._SWEPT_AGE)
        self._card(db, diego, days_old=self._SWEPT_AGE)

        suggestion_jobs.run_absence_sweep()

        assert self._absences(db, rocio) == []
        assert len(self._absences(db, diego)) == 1

    def test_the_sweepable_types_are_types_the_engine_can_learn(self) -> None:
        """Un `ABSENCE_SUBJECT_TYPES` con un tipo de más sería una `ValueError` en el job.

        `record_signal` valida contra `SUBJECT_TYPES`, así que el error no aparecería como
        un test roto sino como una excepción en un job de fondo, a las 6:30, en el log.
        """
        assert learning.ABSENCE_SUBJECT_TYPES <= learning.SUBJECT_TYPES

    def test_the_veto_floor_takes_three_absences_worth_of_weight(
        self, db: Session, diego: User
    ) -> None:
        """La aritmética del piso, con tres ausencias del mismo instante.

        Fija el número: `ABSENCE_VALUE` es un quinto de un "no" deliberado, así que hacen
        falta tres para cruzar `_FILTER_EVIDENCE_FLOOR`. Eso es todo lo que este test dice.

        **No** describe un camino alcanzable: tres ausencias del mismo sujeto en el mismo
        instante no las puede producir el barrido, porque entre dos hay como mínimo diez días
        y con ese espaciado la suma nunca llega al piso. Lo alcanzable lo fija
        `test_the_sweep_can_never_veto_a_subject_on_its_own`, y la separación es a propósito:
        si algún día se acortara la gracia o se subiera el valor de la ausencia, el que tiene
        que fallar primero es ese y no este.
        """
        for index in range(3):
            card = self._card(
                db,
                diego,
                days_old=self._SWEPT_AGE,
                title=f"Probá algo con lentejas {index}",
            )
            signals = learning.record_signal(
                db,
                user_id=diego.id,
                signal_type="unused_suggestion",
                subject_type="food",
                subject_name="lentejas",
                value=learning.ABSENCE_VALUE,
                source_type="inferred",
                source_entity_type="suggestion",
                source_entity_id=card.id,
            )
            assert signals is not None
            vetoed = learning.rejected_subjects(_signals(db, diego))
            expected = {("food", "lentejas")} if index == 2 else set()
            assert vetoed == expected, f"con {index + 1} ausencia(s)"

    def test_the_sweep_can_never_veto_a_subject_on_its_own(self, db: Session, diego: User) -> None:
        """Ninguna cantidad de ausencias saca un alimento de la lista. Y es una cuenta.

        Dos ausencias del mismo sujeto no pueden estar a menos de
        `ABSENCE_GRACE_DAYS + _SNOOZE_DAYS` días una de otra: la tarjeta que produjo la
        primera sigue pendiente hasta que algo la mueva, lo único que la mueve sin escribir su
        propia señal es el "más tarde" —que la suprime `_SNOOZE_DAYS` días—, y la tarjeta que
        venga después tiene que cumplir su propia semana de gracia antes de contar. Con ese
        espaciado y la semivida implícita de 21 días, `_FILTER_DECAY_FLOOR` deja vivas tres
        ausencias como máximo, y esas tres suman ~0.447: abajo de `_FILTER_EVIDENCE_FLOOR`
        para siempre, porque la cuarta entra justo cuando la primera se cae.

        O sea que `_FILTER_EVIDENCE_FLOOR` preserva el comportamiento **por construcción** y
        no por elección de un número: hoy la ausencia solo puede reordenar. Este test es el
        que tiene que fallar si eso deja de ser cierto —si se acorta la gracia, si sube
        `ABSENCE_VALUE` o si se alarga la semivida—, y entonces el panel tiene que dejar de
        prometer que lo aprendido sin apretar nada no filtra nada.
        """
        spacing = learning.ABSENCE_GRACE_DAYS + SuggestionService._SNOOZE_DAYS
        now = datetime.now(UTC)
        #: Ocho es holgado a propósito: la saturación pasa en la tercera, y las cinco de más
        #: son para que el test no dependa de que la cuenta sea exactamente esa.
        for index in range(8):
            card = self._card(
                db,
                diego,
                days_old=self._SWEPT_AGE + index * spacing,
                title=f"Probá algo con lentejas {index}",
            )
            signal = learning.record_signal(
                db,
                user_id=diego.id,
                signal_type="unused_suggestion",
                subject_type="food",
                subject_name="lentejas",
                value=learning.ABSENCE_VALUE,
                source_type="inferred",
                source_entity_type="suggestion",
                source_entity_id=card.id,
            )
            assert signal is not None
            signal.created_at = now - timedelta(days=index * spacing)
            db.flush()

            assert (
                learning.rejected_subjects(_signals(db, diego)) == set()
            ), f"con {index + 1} ausencia(s) espaciadas {spacing} días"

    def test_a_rejection_vetoes_until_it_stops_being_fresh_and_not_a_day_longer(
        self, db: Session, diego: User
    ) -> None:
        """Los dos pisos valen 0.5 y eso los hace la misma condición para un rechazo.

        Un "no" deliberado pesa `1.0 * decay`, así que `peso >= _FILTER_EVIDENCE_FLOOR` y
        `decay >= _FILTER_DECAY_FLOOR` son literalmente la misma desigualdad, y el veto dura
        exactamente una semivida: 90 días para una preferencia dicha con palabras. Si alguno
        de los dos números se moviera sin el otro, el filtro cambiaría de duración sin que
        nada más lo dijera — este test es el que se da cuenta.
        """
        half_life = learning._HALF_LIFE_DAYS["explicit"]
        now = datetime.now(UTC)
        signal = learning.record_signal(
            db,
            user_id=diego.id,
            signal_type="rejected_suggestion",
            subject_type="food",
            subject_name="hígado",
            value=-1.0,
            source_type="explicit",
        )
        assert signal is not None

        signal.created_at = now - timedelta(days=half_life - 1)
        db.flush()
        assert learning.rejected_subjects(_signals(db, diego)) == {("food", "higado")}

        signal.created_at = now - timedelta(days=half_life + 1)
        db.flush()
        assert learning.rejected_subjects(_signals(db, diego)) == set()

    def test_a_single_deliberate_rejection_still_vetoes(self, db: Session, diego: User) -> None:
        """El umbral nuevo no cambia el caso viejo, que es la razón de que valga 0.5.

        Un rechazo fresco pesa 1.0, así que cruza el piso solo. Si no lo hiciera, la 4.4.10
        habría aflojado el filtro que la 4.4.2 puso — y "dije que no y me lo volvió a
        ofrecer" es peor que cualquier cosa que la ausencia pueda aportar.
        """
        learning.record_signal(
            db,
            user_id=diego.id,
            signal_type="rejected_suggestion",
            subject_type="food",
            subject_name="hígado",
            value=-1.0,
            source_type="explicit",
        )

        assert learning.rejected_subjects(_signals(db, diego)) == {("food", "higado")}

    def test_an_absence_only_subject_shows_no_record_count(self, db: Session, diego: User) -> None:
        """Lo que el panel tiene que poder decir de un sujeto que solo tiene ausencias.

        La opinión existe —la ausencia mueve la afinidad— pero no hay ningún registro ni
        ninguna fecha en que el sujeto se haya visto. Contarlo como "1 registro" convertiría
        el número que el panel promete en la suma de dos cosas distintas.
        """
        card = self._card(db, diego, days_old=self._SWEPT_AGE)
        learning.record_signal(
            db,
            user_id=diego.id,
            signal_type="unused_suggestion",
            subject_type="food",
            subject_name="lentejas",
            value=learning.ABSENCE_VALUE,
            source_type="inferred",
            source_entity_type="suggestion",
            source_entity_id=card.id,
        )

        rows = learning.learned_subjects(_signals(db, diego))
        assert len(rows) == 1
        row = rows[0]
        assert row.observations == 0
        assert row.said_observations == 0
        assert row.absence_observations == 1
        assert row.last_seen is None
        assert row.days_since is None
        #: Y el nombre para mostrar sale igual de la señal, no de la clave normalizada.
        assert row.display_name == "lentejas"
        assert row.affinity.direction < 0


def _string_constants_outside_the_vocabulary() -> set[str]:
    """Todas las cadenas literales de `app/`, salvo las del módulo que las declara.

    Por AST y no por `grep` a propósito: un comentario que menciona un tipo de señal no
    es un escritor, y la lista de ejemplos de `app/models/signal.py` —que incluía
    `rejected_activity` y `ingredient_pairing`, ninguno escrito nunca— es justamente el
    tipo de mención que un grep contaría como si alcanzara.
    """
    vocabulary_module = APP_ROOT / "recommendations" / "learning.py"
    found: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        if path == vocabulary_module:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def test_every_signal_type_the_reader_knows_has_a_writer() -> None:
    """La red contra el bug de `repeated_purchase`, una capa más arriba.

    El scorer leía `repeated_purchase` como señal positiva y ningún código lo escribía
    nunca: el lector y los escritores no tenían un vocabulario en común que alguien
    pudiera revisar de un lado solo. Ahora lo tienen —`learning.POSITIVE_SIGNAL_TYPES` y
    `NEGATIVE_SIGNAL_TYPES`—, y este test es lo que impide que se separen otra vez:
    declarar un tipo que nadie escribe falla acá, en el commit que lo declara, y no
    meses después cuando alguien se pregunta por qué la app no aprendió nada.

    Lo que verifica es estructural: que el nombre aparezca como literal en algún otro
    módulo de `app/`. No prueba que la escritura sea correcta —de eso se ocupan los
    tests de arriba, uno por escritor—, prueba que exista.
    """
    written = _string_constants_outside_the_vocabulary()
    known = learning.POSITIVE_SIGNAL_TYPES | learning.NEGATIVE_SIGNAL_TYPES
    orphaned = sorted(known - written)
    assert not orphaned, (
        "Estos tipos de señal se leen y nadie los escribe, que es el bug que la 4.4 "
        f"arregló: {orphaned}. O se les da un escritor, o salen de las listas de "
        "`app/recommendations/learning.py`."
    )


def test_every_subject_type_the_engine_learns_has_a_place_in_the_panel() -> None:
    """Lo mismo que el test de arriba, para el otro vocabulario: los tipos de sujeto.

    El panel de la 4.4.8 recorre `GROUP_ORDER` —una tupla, porque un conjunto no tiene
    orden y una tabla que se reordena entre dos visitas parece que estuviera aprendiendo
    cuando no pasó nada— y `learned_subjects` devuelve solo tipos de `SUBJECT_TYPES`. Los
    dos tienen que cubrir exactamente lo mismo: un tipo que el motor aprende y el panel no
    lista es la opacidad que la 4.4.8 vino a arreglar, y al revés es un grupo que no puede
    tener filas.

    Este test es lo que sostiene que `learned_profile` no necesite una rama para los tipos
    que falten. Sin él, esa rama sería código defensivo que nadie ejecuta —y que por lo
    tanto nadie sabe si funciona— en lugar de una obligación verificada.
    """
    assert set(GROUP_ORDER) == learning.SUBJECT_TYPES
