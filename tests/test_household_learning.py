"""Lo que la casa recibe se filtra contra sus dos personas, y no del mismo modo (4.4.9).

`generate_for_household` no filtraba nada: el comentario decía que el filtrado por persona
se saltea y era literal. Como el generador de despensa propone alimentos concretos, la
lista de compras podía traer justo lo que una de las dos no puede comer.

Estos tests fijan la asimetría, que es la parte que se puede "simplificar" por accidente:
un bloqueo declarado de una persona alcanza para toda la casa, un rechazo aprendido de una
persona no. Si alguien unifica las dos reglas en una, la mitad de este archivo se cae.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryStock
from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations.engine import RecommendationEngine
from app.recommendations.filters import HouseholdMember, apply_household_constraints


def _rejection(user: User, subject_name: str, *, subject_type: str = "food") -> BehaviorSignal:
    """Un "no" explícito y fresco, que es lo único que `rejected_subjects` cuenta."""
    return BehaviorSignal(
        user_id=user.id,
        signal_type="rejected_suggestion",
        entity_type=subject_type,
        entity_name=subject_name,
        value=-1.0,
        source_type="explicit",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )


def _shopping(subject_name: str) -> dict[str, Any]:
    """Un candidato como los que emite `pantry_generator` para un alimento que falta."""
    return {
        "title": f"Comprar {subject_name}",
        "text": f"No queda {subject_name} en la despensa.",
        "category": "shopping",
        "confidence": 0.6,
        "subject_type": "food",
        "subject_name": subject_name,
    }


def _blocked(user: User, item_name: str) -> RecommendationPreference:
    return RecommendationPreference(
        user_id=user.id,
        item_type="food",
        item_name=item_name,
        preference_signal="impossible",
        strength=1.0,
    )


class TestHouseholdHardConstraintsUnite:
    """Un bloqueo declarado de una persona vale para la casa entera."""

    def test_one_members_block_removes_the_candidate(self, diego: User, rocio: User) -> None:
        """Si a uno el maní le hace mal, la casa no lo compra — sin promediar con el otro.

        El costo de equivocarse no es simétrico: de un lado una compra de más, del otro una
        comida que alguien no puede comer.
        """
        members = [
            HouseholdMember(user=diego, preferences=[_blocked(diego, "mani")]),
            HouseholdMember(user=rocio),
        ]
        kept = apply_household_constraints([_shopping("mani"), _shopping("pollo")], members)
        assert [c["subject_name"] for c in kept] == ["pollo"]

    def test_a_block_from_the_other_member_counts_the_same(self, diego: User, rocio: User) -> None:
        """Y da igual de quién sea: no hay un miembro cuyos bloqueos pesen más."""
        members = [
            HouseholdMember(user=diego),
            HouseholdMember(user=rocio, preferences=[_blocked(rocio, "mani")]),
        ]
        kept = apply_household_constraints([_shopping("mani")], members)
        assert kept == []


class TestHouseholdLearnedNegativesIntersect:
    """Un "no" aprendido de una sola persona no es un "no" de la casa."""

    def test_one_members_rejection_does_not_remove_the_others_food(
        self, diego: User, rocio: User
    ) -> None:
        """El caso que motiva la regla: Rocío rechazó el pollo, Diego lo come siempre.

        Con los rechazos unidos, la casa dejaba de comprar pollo por la conducta de una
        sola persona. Un rechazo de conducta no es una restricción declarada: dice "a mí
        no", no "acá no".
        """
        members = [
            HouseholdMember(user=diego),
            HouseholdMember(user=rocio, signals=[_rejection(rocio, "pollo")]),
        ]
        kept = apply_household_constraints([_shopping("pollo")], members)
        assert [c["subject_name"] for c in kept] == ["pollo"]

    def test_a_subject_both_rejected_is_removed(self, diego: User, rocio: User) -> None:
        """Cuando las dos dijeron que no, seguir ofreciéndolo es no haber escuchado."""
        members = [
            HouseholdMember(user=diego, signals=[_rejection(diego, "higado")]),
            HouseholdMember(user=rocio, signals=[_rejection(rocio, "higado")]),
        ]
        kept = apply_household_constraints([_shopping("higado"), _shopping("pollo")], members)
        assert [c["subject_name"] for c in kept] == ["pollo"]

    def test_rejections_of_different_subjects_remove_nothing(
        self, diego: User, rocio: User
    ) -> None:
        """Dos "no" no se suman a uno: la intersección es por sujeto, no por cantidad.

        Sin esto, "cada uno rechazó algo" podría leerse como "la casa rechazó las dos
        cosas", que es el error de unir los conjuntos con otro nombre.
        """
        members = [
            HouseholdMember(user=diego, signals=[_rejection(diego, "higado")]),
            HouseholdMember(user=rocio, signals=[_rejection(rocio, "pollo")]),
        ]
        kept = apply_household_constraints([_shopping("higado"), _shopping("pollo")], members)
        assert {c["subject_name"] for c in kept} == {"higado", "pollo"}

    def test_the_same_no_written_twice_by_one_person_is_still_one_person(
        self, diego: User, rocio: User
    ) -> None:
        """Diez rechazos de una persona siguen siendo una persona.

        La intersección se calcula sobre conjuntos por miembro justamente para que insistir
        no equivalga a convencer al otro.
        """
        members = [
            HouseholdMember(user=diego, signals=[_rejection(diego, "higado") for _ in range(10)]),
            HouseholdMember(user=rocio),
        ]
        kept = apply_household_constraints([_shopping("higado")], members)
        assert [c["subject_name"] for c in kept] == ["higado"]


class TestHouseholdFilterEdges:
    def test_no_members_keeps_every_candidate(self) -> None:
        """La falla segura: sin miembros no hay nadie de quien proteger a nadie.

        La intersección de cero conjuntos sería "todo rechazado" y el filtro borraría la
        lista entera — una casa sin usuarios dejaría de recibir sugerencias en vez de
        recibirlas sin filtrar.
        """
        candidates = [_shopping("pollo"), _shopping("higado")]
        assert apply_household_constraints(candidates, []) == candidates

    def test_a_candidate_without_subject_is_not_dropped_by_the_intersection(
        self, diego: User, rocio: User
    ) -> None:
        """Los avisos de stock del generador no declaran un alimento; no se los filtra por eso."""
        alert = {
            "title": "Se está acabando el stock",
            "text": "Hay 3 cosas por reponer.",
            "category": "shopping",
            "confidence": 0.5,
            "subject_type": "habit",
            "subject_name": "low stock alert",
        }
        members = [
            HouseholdMember(user=diego, signals=[_rejection(diego, "higado")]),
            HouseholdMember(user=rocio, signals=[_rejection(rocio, "higado")]),
        ]
        assert apply_household_constraints([alert], members) == [alert]


class TestEngineReadsEachPersonSeparately:
    def test_members_carry_only_their_own_signals(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """`_household_members` lee por `user_id`, no por `household_id`.

        Es la regla 4 de `AGENTS.md`, y acá además es lo que hace posible la intersección:
        un `WHERE household_id = ?` traería las señales de los dos revueltas y de ahí no se
        puede volver a separar quién dijo qué.
        """
        db.add(_rejection(diego, "higado"))
        db.add(_rejection(rocio, "pollo"))
        db.flush()

        members = RecommendationEngine()._household_members(db, household.id)

        assert {m.user.id for m in members} == {diego.id, rocio.id}
        by_user = {m.user.id: m for m in members}
        assert [s.entity_name for s in by_user[diego.id].signals] == ["higado"]
        assert [s.entity_name for s in by_user[rocio.id].signals] == ["pollo"]

    def test_members_carry_only_their_own_preferences(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        db.add(_blocked(diego, "mani"))
        db.flush()

        members = RecommendationEngine()._household_members(db, household.id)
        by_user = {m.user.id: m for m in members}

        assert [p.item_name for p in by_user[diego.id].preferences] == ["mani"]
        assert by_user[rocio.id].preferences == []


class TestGenerateForHouseholdRunsTheFilter:
    """El filtro tiene que estar **enchufado**, no solo existir.

    Los tests de arriba prueban la función; estos prueban que la corrida la llama. Sin
    ellos, borrar la línea de `generate_for_household` deja la suite entera en verde.
    """

    @staticmethod
    def _out_of_stock(db: Session, household: Household, name: str) -> None:
        food = FoodItem(canonical_name=name, category="other", base_unit="g")
        db.add(food)
        db.flush()
        db.add(
            PantryStock(
                household_id=household.id,
                food_item_id=food.id,
                current_quantity=0,
                unit="g",
                low_stock_threshold=100,
            )
        )
        db.flush()

    def test_a_food_one_member_cannot_eat_does_not_reach_the_shopping_list(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        self._out_of_stock(db, household, "mani")
        db.add(_blocked(diego, "mani"))
        db.flush()

        created = RecommendationEngine().generate_for_household(db, household, limit=5)

        assert not any("mani" in s.text.lower() for s in created)

    def test_the_same_stock_reaches_the_list_when_nobody_blocks_it(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """El control del test de arriba: sin bloqueo, la tarjeta sí sale.

        Sin este par, "no salió nada" podría deberse a que el generador no produjo nada.
        """
        self._out_of_stock(db, household, "mani")

        created = RecommendationEngine().generate_for_household(db, household, limit=5)

        assert any("mani" in s.text.lower() for s in created)

    def test_an_alert_card_naming_a_blocked_food_is_dropped_whole(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Un costo conocido, escrito como decisión y no como accidente.

        La tarjeta de "se acabaron estas cosas" nombra hasta cinco alimentos en su texto, y
        el matcher de bloqueos duros mira `title + text` —así funciona `apply_hard_constraints`
        desde antes de la 4.4.9, en el camino personal también—. Así que si uno de los cinco
        es un alimento bloqueado, se cae la tarjeta entera y con ella el recordatorio de los
        otros cuatro.

        Se acepta en esta dirección a propósito: la alternativa es dejar pasar una tarjeta
        que nombra lo que alguien no puede comer, y el aviso por ítem —el de `low_stock` de
        la 4.3, que sí se retira cuando el ítem se repone— no depende de esta tarjeta.
        """
        self._out_of_stock(db, household, "mani")
        self._out_of_stock(db, household, "arroz")
        db.add(_blocked(diego, "mani"))
        db.flush()

        created = RecommendationEngine().generate_for_household(db, household, limit=5)

        assert not any(s.subject_name == "out of stock alert" for s in created)
