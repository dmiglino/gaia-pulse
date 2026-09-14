"""El generador de comidas: la tarjeta del hueco de macros (4.5.3).

Vive en su propio módulo por la misma razón que `test_activity_generator.py`: lo que se mide
acá es una **conducta** —cuándo la app se permite comparar el día de hoy contra el promedio de
la persona, y cuándo se calla— y no un contador de candidatos. `test_recommendations.py` sigue
midiendo que cada generador declare sujetos válidos.

El hilo de todos estos tests es el mismo: la app **no tiene objetivo de macros**, así que la
única afirmación sostenible es "hoy vas más liviano que tu propio promedio a esta hora", y esa
frase tiene cuatro formas de ser mentira —una base que no es base, una captura que no se pudo
medir, una diferencia que es ruido, y un consejo que no se puede accionar—. Cada clase tapa una.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryStock
from app.models.user import User
from app.recommendations.context import MacroTotals, UserContext
from app.recommendations.generators import meal_generator

_MEAL_TYPE = "dinner"


def _context(**overrides: object) -> UserContext:
    """Un contexto armado a mano: los macros son el dato bajo prueba, no la consulta.

    Armarlos sembrando comidas mediría `_macro_totals` otra vez —eso ya lo hace
    `test_user_context.py`— y haría que cada caso de esta suite dependiera de la hora en que
    corre el test.
    """
    base: dict[str, object] = {
        "user_id": 1,
        "now": datetime.now(tz=UTC),
        "today": datetime.now(tz=UTC).date(),
    }
    base.update(overrides)
    return UserContext(**base)  # type: ignore[arg-type]


def _totals(*, days: int, items: int = 6, **macros: float) -> MacroTotals:
    """Un `MacroTotals` con cobertura completa, salvo que el test diga otra cosa."""
    return MacroTotals(items_counted=items, items_total=items, days_counted=days, **macros)


def _short_on_protein() -> UserContext:
    """Hoy 20 g contra una base de 60 g: por debajo del 70%, y con base suficiente."""
    return _context(
        macros_today=_totals(days=1, protein_g=20.0),
        macros_baseline=_totals(days=4, items=24, protein_g=60.0),
    )


def _stock(db: Session, household: Household, food: FoodItem, quantity: float) -> None:
    db.add(
        PantryStock(
            household_id=household.id,
            food_item_id=food.id,
            current_quantity=quantity,
            unit="g",
            low_stock_threshold=50,
        )
    )
    db.flush()


def _food(db: Session, name: str, **macros: float) -> FoodItem:
    food = FoodItem(canonical_name=name, category="other", base_unit="g", **macros)
    db.add(food)
    db.flush()
    return food


@pytest.fixture
def lentils(db: Session) -> FoodItem:
    """Secas, que es como las tiene un catálogo por 100 g: 24 g de proteína y 8 de fibra."""
    return _food(db, "lentils", calories_per_100g=353, protein_g=24.0, fiber_g=7.9)


@pytest.fixture
def chicken(db: Session) -> FoodItem:
    return _food(db, "chicken", calories_per_100g=165, protein_g=27.0, fiber_g=0.0)


@pytest.fixture
def apple(db: Session) -> FoodItem:
    """Ni una cosa ni la otra: 0.3 g de proteína y 2.4 de fibra, debajo de los dos pisos."""
    return _food(db, "apple", calories_per_100g=52, protein_g=0.3, fiber_g=2.4)


@pytest.fixture
def filler(db: Session, household: Household) -> FoodItem:
    """Lo que se lleva las secciones 1 y 2 sin ser fuente de nada, en la menor cantidad.

    No es decorado: las dos primeras secciones se llevan **siempre** el ítem de menor cantidad
    de la despensa, así que en una despensa de un solo alimento la única fuente posible es
    también un sujeto ya tomado, y el caso pasa por la cesión de sujeto en vez de por el
    guardia que el test quiere medir. Un test que pasa por la razón equivocada no protege nada.

    Arroz: 2.6 g de proteína y 0.4 de fibra por 100 g, debajo de los dos pisos. Y 100 g, que
    está por encima del umbral de stock bajo, así que tampoco entra en la sección 4.
    """
    rice = _food(db, "rice", calories_per_100g=130, protein_g=2.6, fiber_g=0.4)
    _stock(db, household, rice, 100)
    return rice


def _generate(db: Session, user: User, context: UserContext) -> list[dict[str, Any]]:
    return meal_generator.generate(db, user, [], context, meal_type=_MEAL_TYPE)


def _macro_cards(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in candidates if c["confidence"] == meal_generator._MACRO_CONFIDENCE]


def _macro_card(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    cards = _macro_cards(candidates)
    return cards[0] if cards else None


class TestTheTrackedMacrosExist:
    """Los nombres de `_MACRO_TRACKED` son atributos de `MacroTotals`, no strings sueltos.

    El generador los lee con `getattr`, así que un nombre mal escrito no falla al importar:
    falla en producción, dentro de un job, la primera vez que alguien tenga base suficiente.
    """

    def test_every_tracked_field_is_a_field_of_macro_totals(self) -> None:
        empty = MacroTotals()
        for field_name, _label in meal_generator._MACRO_TRACKED:
            assert isinstance(getattr(empty, field_name), float)

    def test_every_tracked_field_declares_a_carrier_floor(self) -> None:
        tracked = {field_name for field_name, _ in meal_generator._MACRO_TRACKED}
        assert set(meal_generator._MACRO_CARRIER_PER_100G) == tracked


class TestABaseThatIsNotABase:
    """Sin días suficientes no hay promedio, y sin promedio no hay comparación."""

    def test_an_empty_baseline_says_nothing(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        _stock(db, household, lentils, 500)

        assert _macro_card(_generate(db, diego, _context())) is None

    def test_a_single_recorded_day_is_an_anecdote(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """Un día solo puede haber sido un asado, y no describe ninguna costumbre."""
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=20.0),
            macros_baseline=_totals(days=1, protein_g=60.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None

    def test_enough_days_do_talk(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        _stock(db, household, lentils, 500)

        card = _macro_card(_generate(db, diego, _short_on_protein()))

        assert card is not None
        assert card["subject_name"] == "lentils"
        assert card["subject_type"] == "food"
        assert card["meal_type"] == _MEAL_TYPE


class TestACaptureThatCouldNotBeMeasured:
    """Cobertura baja significa "no se pudo medir", no "comió menos"."""

    def test_a_day_captured_as_free_text_does_not_get_told_it_is_short(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """ "Cené milanesas" deja el ítem sin gramos, y el total baja sin que baje la comida.

        Sin el piso de cobertura, esta tarjeta le avisaría "te falta proteína" justamente a
        quien escribe en castellano en vez de pesar la comida.
        """
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=MacroTotals(
                protein_g=20.0, items_counted=1, items_total=6, days_counted=1
            ),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None

    def test_a_baseline_built_on_unmeasurable_days_does_not_talk_either(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=20.0),
            macros_baseline=MacroTotals(
                protein_g=60.0, items_counted=4, items_total=24, days_counted=4
            ),
        )

        assert _macro_card(_generate(db, diego, context)) is None


class TestOnlyShortfallsAndOnlyDownwards:
    """La escala tiene un solo lado, como el RPE alto de `activity_generator`."""

    def test_a_normal_day_is_not_news(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """55 sobre una base de 60 es la misma costumbre, no un hueco.

        Un umbral pegado al 100% dispararía la mitad de los días por el ruido de un promedio
        de cuatro días.
        """
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=55.0),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None

    def test_eating_more_than_usual_is_never_a_card(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """No hay tarjeta de exceso: sin objetivo, "hoy comiste más grasa" no propone nada.

        Y lo que no propone nada es consejo dietario, que es lo que esta app no puede sostener.
        """
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=90.0, fat_g=200.0),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0, fat_g=50.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None

    def test_calories_are_not_a_tracked_macro(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """Un día liviano en calorías no dispara nada: no hay meta contra la cual pesarlo."""
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, calories=400.0),
            macros_baseline=_totals(days=4, items=24, calories=2000.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None


class TestSomethingHasToBeAbleToCarryIt:
    """Un empujón que no se puede accionar es lo que la app viene a dejar de ser."""

    def test_a_pantry_with_no_source_of_the_macro_stays_quiet(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        apple: FoodItem,
    ) -> None:
        """0.3 g de proteína por 100 g no es una fuente de proteína, y decirlo sería mentir."""
        _stock(db, household, apple, 500)

        candidates = _generate(db, diego, _short_on_protein())

        assert _macro_card(candidates) is None
        assert candidates, "las demás secciones siguen produciendo: el silencio es solo de esta"

    def test_the_best_source_wins_and_the_order_is_declared(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
        chicken: FoodItem,
    ) -> None:
        """27 g le gana a 24 g, y el pollo entra con **más** cantidad para que no gane posición.

        Si el orden fuera el de la despensa —que ordena por cantidad ascendente— saldrían las
        lentejas, y la tarjeta nombraría la segunda mejor fuente.
        """
        _stock(db, household, lentils, 800)
        _stock(db, household, chicken, 900)

        card = _macro_card(_generate(db, diego, _short_on_protein()))

        assert card is not None
        assert card["subject_name"] == "chicken"

    def test_it_does_not_take_a_subject_another_section_already_took(
        self, db: Session, diego: User, household: Household, chicken: FoodItem
    ) -> None:
        """El pollo es lo único con proteína **y** el sujeto de la sección 1, así que se cede.

        Sin relleno a propósito: es el único caso donde la colisión de sujeto es el objeto de
        la prueba y no un accidente del fixture.

        El de-dup final es por **título**, así que dos tarjetas con un mismo sujeto sobreviven
        las dos: se estorban en la lista y el feedback de una enseña sobre la otra.
        """
        _stock(db, household, chicken, 40)

        candidates = _generate(db, diego, _short_on_protein())

        assert _macro_card(candidates) is None
        assert {str(c["subject_name"]).lower() for c in candidates} == {"chicken"}

    def test_it_falls_back_to_the_next_source_when_the_first_is_taken(
        self, db: Session, diego: User, household: Household, lentils: FoodItem, chicken: FoodItem
    ) -> None:
        """Ceder el sujeto no es callarse: si hay otra fuente, la tarjeta la usa.

        El pollo tiene la menor cantidad, así que encabeza la sección 1; las lentejas quedan
        libres y son fuente de proteína igual. Acá el relleno lo hace el propio pollo.
        """
        _stock(db, household, chicken, 40)
        _stock(db, household, lentils, 900)

        card = _macro_card(_generate(db, diego, _short_on_protein()))

        assert card is not None
        assert card["subject_name"] == "lentils"

    def test_a_disliked_source_is_not_a_source(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        chicken: FoodItem,
    ) -> None:
        diego.disliked_foods_json = ["chicken"]
        db.flush()
        _stock(db, household, chicken, 900)

        assert _macro_card(_generate(db, diego, _short_on_protein())) is None


class TestTheTiebreakIsDeclared:
    def test_protein_wins_over_fiber_and_only_one_card_comes_out(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """Los dos macros cortos son la misma cena, así que sale una sola tarjeta.

        Y sale la de proteína porque el orden de `_MACRO_TRACKED` está declarado, no derivado
        del orden de un `dict`.
        """
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=20.0, fiber_g=3.0),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0, fiber_g=25.0),
        )

        cards = _macro_cards(_generate(db, diego, context))

        assert len(cards) == 1
        assert "protein" in cards[0]["title"]

    def test_fiber_talks_when_protein_is_fine(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        _stock(db, household, lentils, 500)
        context = _context(
            macros_today=_totals(days=1, protein_g=60.0, fiber_g=3.0),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0, fiber_g=25.0),
        )

        card = _macro_card(_generate(db, diego, context))

        assert card is not None
        assert "fiber" in card["title"]

    def test_fiber_needs_a_source_of_fiber_and_not_just_any_food(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        chicken: FoodItem,
    ) -> None:
        """El pollo tiene 0 g de fibra: es fuente de proteína, no de la que falta hoy."""
        _stock(db, household, chicken, 900)
        context = _context(
            macros_today=_totals(days=1, protein_g=60.0, fiber_g=3.0),
            macros_baseline=_totals(days=4, items=24, protein_g=60.0, fiber_g=25.0),
        )

        assert _macro_card(_generate(db, diego, context)) is None


class TestItSaysWhatItMeasured:
    def test_the_text_carries_both_numbers_and_claims_no_deficit(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """ "Te faltan 40 g" necesita un objetivo, y la app no tiene ninguno.

        Lo que sí se midió son los dos números, y con los dos a la vista la persona decide si
        le importa.
        """
        _stock(db, household, lentils, 500)

        card = _macro_card(_generate(db, diego, _short_on_protein()))

        assert card is not None
        assert "20 g" in card["text"] and "60 g" in card["text"]
        assert "lentils" in card["text"]

    def test_the_evidence_says_over_how_many_days_and_items(
        self,
        db: Session,
        diego: User,
        household: Household,
        filler: FoodItem,
        lentils: FoodItem,
    ) -> None:
        """La cobertura y los días no son adorno: son lo que hace auditable la comparación."""
        _stock(db, household, lentils, 500)

        card = _macro_card(_generate(db, diego, _short_on_protein()))

        assert card is not None
        assert "4 recorded days" in card["evidence_summary"]
        assert "6 of 6 logged items" in card["evidence_summary"]
