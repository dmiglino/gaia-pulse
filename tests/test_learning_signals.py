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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.signal import BehaviorSignal
from app.models.user import User
from app.recommendations import learning
from app.recommendations.scorer import score_candidates
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.schemas.pantry import PurchaseItem, PurchaseRequest
from app.schemas.workout import (
    WorkoutExerciseCreate,
    WorkoutParticipantCreate,
    WorkoutSessionCreate,
)
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
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
            timestamp=datetime.now(timezone.utc),
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
                timestamp_start=datetime.now(timezone.utc),
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
                timestamp_start=datetime.now(timezone.utc),
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

        candidate = {
            "title": "Ensalada de quinoa",
            "confidence": 0.5,
            "subject_type": "food",
            "subject_name": "quinoa",
        }
        (scored,) = score_candidates([candidate], diego, _signals(db, diego), [])
        assert scored["_score"] > 0.5

    def test_signals_older_than_the_window_stop_counting(
        self, db: Session, household: Household, diego: User
    ) -> None:
        """El scorer mira 30 días. Sin esto, "comí esto una vez en 2024" pesa para siempre."""
        _log_meal(
            db,
            household,
            participants=[
                MealParticipantCreate(
                    user_id=diego.id, items=[MealItemCreate(food_name="ravioles")]
                )
            ],
        )
        (signal,) = _signals(db, diego)
        signal.created_at = datetime.now(timezone.utc) - timedelta(days=200)
        db.flush()

        candidate = {
            "title": "Ravioles caseros",
            "confidence": 0.5,
            "subject_type": "food",
            "subject_name": "ravioles",
        }
        (scored,) = score_candidates([candidate], diego, _signals(db, diego), [])
        assert scored["_score"] == 0.5


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
