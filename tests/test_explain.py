"""Que la explicación de una sugerencia sea la razón, y no una frase de catálogo.

Lo que estos tests cuidan no es el fraseo —eso va a cambiar— sino las tres reglas que
separan explicar de decorar, y que son exactamente las que la versión anterior rompía: un
eje con delta 0 no se nombra, lo que sube y lo que baja no se mezclan en una sola frase, y
cuando no hay ni medición ni aprendizaje la tarjeta lo dice en vez de inventar una razón.

El cuarto es estructural: recorre los generadores buscando `rationale` fijos. Es el que
impide que la 4.5.4 se deshaga de a una cadena por vez, que es cómo se juntaron las nueve
que había.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.recommendations import explain
from app.recommendations.explain import ScorePart


def _parts(*specs: tuple[str, float, str]) -> list[ScorePart]:
    return [ScorePart(axis, delta, label) for axis, delta, label in specs]


class TestLearnedHalf:
    def test_zero_delta_axis_is_never_named(self) -> None:
        """Un eje que no movió nada no aparece. Enumerar ceros es volver a la plantilla."""
        half = explain.learned_half(
            _parts(
                (explain.AXIS_SUBJECT, 0.12, "milanesas"),
                (explain.AXIS_ATTRIBUTE, 0.0, "protein"),
                (explain.AXIS_SLOT, 0.0, "dinner"),
            )
        )
        assert "milanesas" in half
        assert "protein" not in half
        assert "dinner" not in half

    def test_ups_and_downs_land_in_separate_sentences(self) -> None:
        """El signo neto ocultaría que las dos cosas pasaron, así que van aparte."""
        half = explain.learned_half(
            _parts(
                (explain.AXIS_SUBJECT, 0.12, "café"),
                (explain.AXIS_SATIETY, -0.15, "café"),
            )
        )
        assert half.count(".") == 2
        up, down = half.split(". ")
        assert up.startswith("Ranked up because")
        assert down.startswith("Ranked down because")
        assert "very recently" in down

    def test_axis_order_governs_the_wording(self) -> None:
        """El texto sigue `AXIS_ORDER` y no el orden en que el scorer calculó sus ejes."""
        forwards = explain.learned_half(
            _parts(
                (explain.AXIS_SUBJECT, 0.1, "espinaca"),
                (explain.AXIS_ATTRIBUTE, 0.05, "vegetable"),
                (explain.AXIS_SLOT, 0.03, "lunch"),
            )
        )
        backwards = explain.learned_half(
            _parts(
                (explain.AXIS_SLOT, 0.03, "lunch"),
                (explain.AXIS_ATTRIBUTE, 0.05, "vegetable"),
                (explain.AXIS_SUBJECT, 0.1, "espinaca"),
            )
        )
        assert forwards == backwards
        assert forwards.index("espinaca") < forwards.index("vegetable") < forwards.index("lunch")

    def test_join_reads_like_a_sentence_for_one_two_and_three(self) -> None:
        assert explain._join(["a"]) == "a"
        assert explain._join(["a", "b"]) == "a and b"
        assert explain._join(["a", "b", "c"]) == "a, b and c"

    def test_a_positive_satiety_is_skipped_rather_than_phrased(self) -> None:
        """Saciedad y diversidad solo restan; con signo invertido no se les inventa frase."""
        assert explain.learned_half(_parts((explain.AXIS_SATIETY, 0.15, "café"))) == ""
        assert explain.learned_half(_parts((explain.AXIS_DIVERSITY, 0.20, "café"))) == ""

    def test_an_unknown_axis_is_skipped(self) -> None:
        assert explain.learned_half(_parts(("mood", 0.5, "whatever"))) == ""

    def test_a_part_without_label_still_reads(self) -> None:
        half = explain.learned_half(_parts((explain.AXIS_SUBJECT, 0.1, "")))
        assert half == "Ranked up because you have said yes to it before."


class TestRationale:
    def test_both_halves_are_joined(self) -> None:
        text = explain.rationale(
            {
                "rationale": "Milk has the least left of anything in your pantry.",
                explain.PARTS_KEY: _parts((explain.AXIS_SUBJECT, 0.12, "milk")),
            }
        )
        assert text.startswith("Milk has the least left")
        assert "Ranked up because you have said yes to milk before." in text

    def test_a_household_shaped_item_keeps_only_the_measured_half(self) -> None:
        """`generate_for_household` no puntúa: no hay señales de una persona que leer."""
        text = explain.rationale({"rationale": "You are down to your last two eggs."})
        assert text == "You are down to your last two eggs."

    def test_a_scored_item_with_no_measurement_keeps_only_the_learned_half(self) -> None:
        text = explain.rationale(
            {explain.PARTS_KEY: _parts((explain.AXIS_DIVERSITY, -0.2, "eggs"))}
        )
        assert text == "Ranked down because eggs already came up in the last few days."

    def test_nothing_known_says_so(self) -> None:
        assert explain.rationale({}) == explain.NOTHING_KNOWN
        assert explain.rationale({"rationale": "   ", explain.PARTS_KEY: []}) == (
            explain.NOTHING_KNOWN
        )
        assert (
            explain.rationale({"rationale": None, explain.PARTS_KEY: None}) == explain.NOTHING_KNOWN
        )


class TestNoFixedRationalesLeft:
    """Que ningún `rationale` de un generador vuelva a ser una cadena literal.

    Sirve para lo que un test de comportamiento no alcanza: una frase fija nueva pasa todos
    los tests de generador porque el campo sigue siendo un `str`. `pantry_generator` queda
    afuera a propósito —sus cuatro cadenas son la 4.5.7 y listarlo acá haría fallar el test
    por trabajo que todavía no toca—; `blood_generator` entró con la 4.5.6, cuando sus 22
    razones pasaron a citar el valor medido y la fecha del panel.
    """

    _GENERATORS = ("meal_generator.py", "activity_generator.py", "blood_generator.py")

    #: La única excepción, y con nombre para que agregar otra cueste una decisión: la tarjeta
    #: de descanso se dispara con `days_since == 0` y no hay número que interpolar — "hoy" es
    #: la medición entera. Sigue siendo la razón de **esta** tarjeta y no fisiología general,
    #: que es lo que decía antes. Cualquier otra constante hace fallar el test.
    _ALLOWED = {
        "You already logged a session today, so what is missing is recovery, "
        "not another stimulus."
    }

    def test_every_rationale_is_computed(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "recommendations" / "generators"
        offenders: list[str] = []
        for filename in self._GENERATORS:
            tree = ast.parse((root / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values, strict=True):
                    if not (isinstance(key, ast.Constant) and key.value == "rationale"):
                        continue
                    #: Una `JoinedStr` es un f-string y un `Name` es una variable: las dos
                    #: dependen de datos de esta corrida. Lo que se rechaza es la constante
                    #: y la concatenación implícita de constantes, que es la forma que
                    #: tenían las nueve.
                    if isinstance(value, ast.Constant) and value.value not in self._ALLOWED:
                        offenders.append(f"{filename}:{value.lineno}")
        assert offenders == [], (
            "rationale fijo: la explicación tiene que citar un dato de esta corrida, "
            f"no una frase de catálogo ({', '.join(offenders)})"
        )
