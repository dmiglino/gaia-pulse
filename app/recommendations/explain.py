"""Cómo una sugerencia dice por qué existe.

Hasta la 4.5.3 el `rationale` —lo que se muestra bajo *"¿Por qué esta sugerencia?"*— era una
cadena fija por regla: los ocho candidatos de una corrida se repartían cinco frases, y
`"Dietary variety supports micronutrient balance."` era literalmente la misma para cualquier
alimento de cualquier persona en cualquier día. Lo que de verdad había decidido el orden
—qué señal subió o bajó el score— vivía en `logger.debug` y se descartaba. La app decía por
qué, y no era la razón.

Una explicación tiene acá **dos mitades**, y las escriben dos capas distintas:

- **El dato**: qué medición produjo la tarjeta. La pone el generador, que es el único que
  tiene los números, y llega en la clave `rationale` del candidato.
- **El aprendizaje**: qué movió la tarjeta en el orden. Lo pone el scorer, que es el único
  que tiene los ejes, y llega en `PARTS_KEY` como una lista de `ScorePart`.

`rationale()` las junta, y es lo único que el motor llama. Una sugerencia de la casa pasa
por acá con una sola mitad y eso está bien: `generate_for_household` no puntúa —no hay una
persona cuyas señales leer— así que su explicación es la medición y nada más.

Qué es cada campo, porque los tres se parecen y se pisaban: `text` es la propuesta dirigida
a la persona, `evidence_summary` es el rastro auditable con los números crudos, y `rationale`
contesta **por qué esta tarjeta y no otra** — la regla de selección instanciada con los
valores reales, más lo que el orden le hizo.

Tres reglas que separan explicar de decorar:

- **Ningún eje se nombra cuando su delta es 0.** Enumerar los ceros —"no aprendimos nada de
  la categoría, no aprendimos nada de la hora"— es exactamente cómo una explicación vuelve a
  ser una plantilla: larga, idéntica en todas las tarjetas y sin información.
- **Lo que sube y lo que baja van en frases separadas.** Un único "subió porque…" con signos
  mezclados sería falso, y el signo neto tampoco alcanza: el neto de +0.12 y −0.20 no dice
  que las dos cosas pasaron.
- **Cuando no hay ni dato ni aprendizaje, la tarjeta lo dice.** Caer en una frase de catálogo
  cuando no se sabe nada es la mentira que esto viene a sacar.

**Interacción con i18n, que la Fase 5 tiene que saber antes de contar msgids:** el
`rationale` se persiste **ya renderizado**, así que queda congelado en el idioma en que se
generó — un cambio de idioma no reescribe las sugerencias viejas, y estas frases no son
msgids que se puedan dar vuelta al renderizar. Es un tradeoff aceptado y no un bug: guardar
la explicación de forma que se pueda rehidratar en cualquier idioma significa guardar los
`ScorePart` y las mediciones de cada generador en la fila, que es una migración —y `0003`
está gastada— más un renderizador en la plantilla. La consecuencia práctica es que las
cadenas de este módulo y de los generadores **no** pasan por `_()`: un job de fondo no tiene
locale de request, así que envolverlas solo elegiría el idioma del proceso y daría la
ilusión de que son traducibles.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: La clave con la que el scorer deja sus partes en el candidato. Lleva guion bajo como
#: `_score`: es material de la corrida, no un campo de la sugerencia. Nada de esto se
#: persiste — lo que se persiste es la frase que sale de `rationale()`.
PARTS_KEY = "_score_parts"

AXIS_SUBJECT = "subject"
AXIS_ATTRIBUTE = "attribute"
AXIS_SLOT = "slot"
AXIS_SATIETY = "satiety"
AXIS_DIVERSITY = "diversity"

#: El orden en que se nombran los ejes, **declarado** y no el de aparición en el scorer, por
#: la misma razón que `meal_generator._MACRO_TRACKED` y
#: `activity_generator._ROTATION_PRIORITY`: un orden que sale de dónde quedó una línea se
#: cambia sin querer al agregar otra, y la explicación de dos corridas idénticas dejaría de
#: leerse igual. De lo más específico a lo más general y los descuentos al final, que es el
#: orden en que una persona lo diría.
AXIS_ORDER: tuple[str, ...] = (
    AXIS_SUBJECT,
    AXIS_ATTRIBUTE,
    AXIS_SLOT,
    AXIS_SATIETY,
    AXIS_DIVERSITY,
)

#: Cómo se dice cada eje según su signo: `(cuando sube, cuando baja)`. La saciedad y la
#: diversidad no tienen forma positiva porque por construcción solo restan (ver
#: `scorer._SATIETY_PENALTY` y `_DIVERSITY_PENALTY`); si alguna vez llegara una con delta
#: positivo se saltea en vez de inventarle una frase, que es lo que hace `_phrase`.
_PHRASES: dict[str, tuple[str, str]] = {
    AXIS_SUBJECT: (
        "you have said yes to {label} before",
        "you have turned {label} down before",
    ),
    AXIS_ATTRIBUTE: (
        "{label} generally goes well for you",
        "{label} generally does not go well for you",
    ),
    AXIS_SLOT: (
        "it lands better at {label} than at other hours",
        "it lands worse at {label} than at other hours",
    ),
    AXIS_SATIETY: ("", "you had {label} very recently"),
    AXIS_DIVERSITY: ("", "{label} already came up in the last few days"),
}

#: Lo que dice una tarjeta detrás de la cual no hay nada: ni una medición del generador ni un
#: eje que la haya movido. Es el piso honesto y el punto de todo el módulo — antes de la
#: 4.5.4 este caso salía con una frase de catálogo indistinguible de una tarjeta que sí tenía
#: razones.
NOTHING_KNOWN = (
    "Nothing measured and nothing learned stands behind this one yet: it comes from a "
    "general rule, and what you tap teaches the app."
)


@dataclass(frozen=True)
class ScorePart:
    """Un eje que efectivamente movió el score, con cuánto lo movió y sobre qué.

    `label` es la cosa concreta que el eje mira —el alimento, la categoría, la franja— y no
    el nombre del eje: "las verduras generalmente te caen bien" explica algo, "el eje
    atributo aportó +0.024" es el log de antes con otro formato.
    """

    axis: str
    delta: float
    label: str = ""


def _phrase(part: ScorePart) -> str:
    """La frase de una parte, o vacío si su eje no tiene forma para ese signo."""
    positive, negative = _PHRASES.get(part.axis, ("", ""))
    template = positive if part.delta > 0 else negative
    return template.format(label=part.label or "it") if template else ""


def _join(phrases: Sequence[str]) -> str:
    """ "a", "a and b", "a, b and c". Sin coma de Oxford, que es la forma corriente acá."""
    if len(phrases) == 1:
        return phrases[0]
    return ", ".join(phrases[:-1]) + f" and {phrases[-1]}"


def learned_half(parts: Sequence[ScorePart]) -> str:
    """Lo que el orden le hizo a la tarjeta, en hasta dos frases: lo que subió y lo que bajó.

    Recorre `AXIS_ORDER` por fuera y las partes por dentro —y no las partes en el orden en
    que llegaron— para que el texto no dependa de en qué orden el scorer calculó sus ejes.
    """
    ups: list[str] = []
    downs: list[str] = []
    for axis in AXIS_ORDER:
        for part in parts:
            if part.axis != axis or not part.delta:
                continue
            phrase = _phrase(part)
            if not phrase:
                continue
            (ups if part.delta > 0 else downs).append(phrase)

    sentences: list[str] = []
    if ups:
        sentences.append(f"Ranked up because {_join(ups)}.")
    if downs:
        sentences.append(f"Ranked down because {_join(downs)}.")
    return " ".join(sentences)


def rationale(candidate: Mapping[str, Any]) -> str:
    """La explicación de *candidate*: la mitad de dato del generador más la de aprendizaje.

    Es el único punto que el motor llama, y por eso las dos formas de armar una sugerencia
    —la de una persona y la de la casa— no pueden divergir en cómo explican.
    """
    measured = str(candidate.get("rationale") or "").strip()
    parts: Sequence[ScorePart] = candidate.get(PARTS_KEY) or ()
    halves = [half for half in (measured, learned_half(parts)) if half]
    if not halves:
        return NOTHING_KNOWN
    return " ".join(halves)
