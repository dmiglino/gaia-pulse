"""Blood analysis-based recommendation generator.

Traduce marcadores fuera de rango en comida y movimiento concretos, y —desde la 4.5.6—
decide primero **si corresponde traducir algo**. Dos reglas gobiernan este archivo, y las
dos son de encuadre antes que de contenido:

- **Un número de laboratorio tiene fecha de vencimiento.** Hasta acá la antigüedad del
  panel no se chequeaba en ningún lado, así que un análisis de hace tres años dictaba el
  consejo de hoy con la misma confianza que uno de la semana pasada. Ahora hay tres bandas
  (`_band`): fresco advierte y nombra la fecha, viejo advierte con la fecha, el reparo y
  menos confianza, y pasado el techo **no se advierte nada** —sale una sola tarjeta
  pidiendo un panel nuevo—.
- **Una app no diagnostica.** Lo que decía cada tarjeta era un imperativo médico con una
  causa afirmada: *"Your LDL (bad cholesterol) is high. Reduce butter, fatty meats…"* con
  `rationale` *"Low hemoglobin may indicate iron deficiency anemia."*. Nombrar una
  condición a partir de un número es el paso que no nos toca. Cada entrada del catálogo
  declara ahora tres cosas separadas —el alimento o el movimiento (`action`), el mecanismo
  que lo liga al marcador (`mechanism`) y el título— y la observación, la fecha y el reparo
  por antigüedad los compone `_compose` **una vez**. Es la misma regla de la 4.5.4: una
  explicación se compone, no se escribe 22 veces. El aviso de "esto no es un diagnóstico"
  vive en la pantalla y no en la tarjeta, por lo que explica `_DISCLAIMER_TEMPLATES`.

Las derivaciones de TSH y creatinina se quedan —decirle a alguien que consulte es lo
correcto, y es lo único que corresponde decir de esos dos marcadores—, pero ya no son la
excepción que elude los filtros: desde la 4.5.5 `category="habit"` se compara contra los
dos conjuntos de bloqueos como todo lo demás.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.models.user import User
from app.recommendations.context import BloodPanel

logger = logging.getLogger(__name__)

#: Los estados que ameritan decir algo. `normal` y cualquier cosa que el parser no
#: reconozca no producen nada.
_ACTIONABLE_STATUSES = frozenset({"low", "critical_low", "high", "critical_high"})
_LOW_STATUSES = frozenset({"low", "critical_low"})
_CRITICAL_STATUSES = frozenset({"critical_low", "critical_high"})
_CRITICAL_CONFIDENCE_BUMP = 0.05

#: Hasta acá el panel se trata como si describiera a la persona de hoy.
_FRESH_DAYS = 180
#: Y a partir de acá no se advierte nada. Los dos números son convención de laboratorio
#: —medio año y un año— y no una derivación de nada; están declarados acá para que
#: moverlos sea una decisión y no un literal escondido en un `if`.
_OBSOLETE_DAYS = 365

#: Cuánto vale un panel de entre seis meses y un año: menos, pero no cero. Bajar el score
#: es la respuesta graduada entre advertir como si fuera de ayer y callarse.
_STALE_CONFIDENCE_FACTOR = 0.8

_FRESH, _STALE, _OBSOLETE, _UNDATED = "fresh", "stale", "obsolete", "undated"

#: El sujeto de la tarjeta que pide un panel nuevo. Es propio y no un marcador: lo que se
#: acepta o se rechaza ahí es "avisame de esto", no un consejo sobre el hierro. Con sujeto
#: propio, además, el dedup de la 4.4 alcanza para que no salga dos veces.
_REFRESH_SUBJECT = "blood_panel_refresh"
_REFRESH_CONFIDENCE = 0.65

#: Las dos pantallas que renderizan estas tarjetas, y que llevan el aviso de no-diagnóstico.
#: No hay constante con esa frase acá **a propósito**: el aviso ya existe, traducido y una
#: sola vez por pantalla, en `ui.notice` de `suggestions/partials/list.html` y de `home.html`,
#: condicionado a que haya al menos una tarjeta con este `source_type`. Repetirlo dentro del
#: `text` de cada tarjeta lo dejaba tres veces en la misma pantalla y en inglés fijo —estas
#: cadenas se persisten renderizadas y no pasan por `_()`—, o sea la misma regla escrita dos
#: veces, una de ellas intraducible. Lo que este archivo sí garantiza es el encuadre: se
#: observa, se propone comida o movimiento, y no se nombra ninguna condición.
#: `tests/test_recommendations.py::TestBloodPanelBands` mide las dos mitades: que ninguna
#: entrada del catálogo use vocabulario diagnóstico y que el aviso siga estando en las dos
#: plantillas — borrar una de ellas es lo que dejaría el consejo sin encuadre.
_DISCLAIMER_TEMPLATES = (
    "app/templates/suggestions/partials/list.html",
    "app/templates/home.html",
)


@dataclass(frozen=True)
class _Advice:
    """Lo que **solo esta entrada** sabe, y nada más que eso.

    Tres campos en vez de un `text` armado: `action` es la comida o el movimiento dirigido
    a la persona, `mechanism` es por qué ese alimento se relaciona con ese marcador —una
    afirmación sobre la comida, nunca sobre el cuerpo de quien lee— y el título nombra la
    ruta y no la orden ("Oats at breakfast", no "Limit saturated fats"). La observación,
    la fecha del panel, el reparo por antigüedad y la línea de no-diagnóstico no están acá
    a propósito: son iguales en las 22 y las agrega `_compose`.
    """

    title: str
    action: str
    mechanism: str
    category: str
    confidence: float


#: Marcador -> banda ("low" | "high") -> tarjetas. Un marcador sin entrada para su banda
#: no produce nada, que es el caso de la mayoría de los valores altos.
_BIOMARKER_ADVICE: dict[str, dict[str, tuple[_Advice, ...]]] = {
    "hemoglobin": {
        "low": (
            _Advice(
                title="Lentils, a few times a week",
                action=(
                    "Lentils are one of the routes people take: they keep in the pantry "
                    "and go into soups and salads without much planning."
                ),
                mechanism=(
                    "iron is what hemoglobin is built from, and lentils carry it along with folate"
                ),
                category="meal",
                confidence=0.85,
            ),
            _Advice(
                title="Spinach alongside something citrus",
                action=(
                    "Spinach with a squeeze of lemon or an orange on the side is the "
                    "cheap version of this."
                ),
                mechanism=(
                    "leafy greens carry non-heme iron, and the vitamin C next to them is "
                    "what makes more of it absorbable"
                ),
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "ferritin": {
        "low": (
            _Advice(
                title="Lean red meat, lentils or tofu",
                action=(
                    "A few servings a week of lean red meat, lentils or tofu is the usual "
                    "way people cover this from food."
                ),
                mechanism=(
                    "ferritin is the form iron is stored in, and those are the denser dietary "
                    "sources of it"
                ),
                category="meal",
                confidence=0.85,
            ),
        ),
        "high": (
            _Advice(
                title="Lighter on red and processed meat",
                action=(
                    "Leaning on other proteins for a while, and not adding iron "
                    "supplements on your own, is the food-side lever here."
                ),
                mechanism=(
                    "red and processed meats are the heme-iron sources that raise stored iron "
                    "fastest"
                ),
                category="meal",
                confidence=0.75,
            ),
        ),
    },
    "vitamin_d": {
        "low": (
            _Advice(
                title="Twenty minutes of morning sun",
                action=(
                    "Twenty minutes outside in the morning light, most days, is the version that "
                    "costs nothing."
                ),
                mechanism=(
                    "skin synthesises vitamin D from sunlight, which no food matches in quantity"
                ),
                category="activity",
                confidence=0.90,
            ),
            _Advice(
                title="Fatty fish twice a week",
                action="Salmon, mackerel or sardines twice a week is the dietary half of it.",
                mechanism="oily fish are among the few foods with meaningful vitamin D in them",
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "vitamin_b12": {
        "low": (
            _Advice(
                title="Eggs, cheese or yogurt daily",
                action=(
                    "Eggs, cheese and yogurt are the easy daily sources — no supplement needed to "
                    "try this first."
                ),
                mechanism=(
                    "B12 comes almost entirely from animal foods, and those are the everyday ones"
                ),
                category="meal",
                confidence=0.85,
            ),
        ),
        "high": (),
    },
    "folate": {
        "low": (
            _Advice(
                title="Leafy greens most days",
                action=(
                    "Kale, spinach, broccoli and asparagus are the densest sources on a normal "
                    "shopping list."
                ),
                mechanism=(
                    "folate is named after foliage, and green leaves are where it concentrates"
                ),
                category="meal",
                confidence=0.85,
            ),
        ),
        "high": (),
    },
    "iron": {
        "low": (
            _Advice(
                title="Iron foods next to vitamin C",
                action=(
                    "Meat or beans with citrus, peppers or tomato on the same plate — the "
                    "pairing matters more than the amount here."
                ),
                mechanism=(
                    "vitamin C converts plant iron into the form the gut absorbs several times "
                    "better"
                ),
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "glucose": {
        "high": (
            _Advice(
                title="A walk after dinner",
                action=(
                    "Thirty minutes of walking after a meal, rather than before it, is "
                    "the one with the most behind it."
                ),
                mechanism=(
                    "muscles pull glucose out of the blood while they work, so timing it after "
                    "eating is what does the work"
                ),
                category="activity",
                confidence=0.90,
            ),
            _Advice(
                title="Whole grains in place of white",
                action=(
                    "Swapping white bread, white rice and sugary drinks for whole grains and fibre "
                    "is the food-side version."
                ),
                mechanism=(
                    "refined carbohydrates arrive as sugar much faster than the same food with its "
                    "fibre intact"
                ),
                category="meal",
                confidence=0.85,
            ),
        ),
        "low": (),
    },
    "cholesterol_total": {
        "high": (
            _Advice(
                title="Oats at breakfast",
                action="Oats at breakfast is the smallest change that shows up in this number.",
                mechanism=(
                    "the beta-glucan in oats is a soluble fibre that binds cholesterol in the gut"
                ),
                category="meal",
                confidence=0.85,
            ),
        ),
        "low": (),
    },
    "ldl": {
        "high": (
            _Advice(
                title="Olive oil in place of butter",
                action=(
                    "Olive oil instead of butter, and less of the fatty meats and full-fat "
                    "dairy, is where most of the change comes from."
                ),
                mechanism=(
                    "saturated fat is the dietary input that moves LDL most, and olive oil is the "
                    "usual swap"
                ),
                category="meal",
                confidence=0.85,
            ),
            _Advice(
                title="Aerobic work three times a week",
                action=(
                    "Cycling, swimming or brisk walking three times a week is the movement-side "
                    "lever."
                ),
                mechanism=(
                    "sustained aerobic work is the habit with the clearest effect on the LDL/HDL "
                    "balance"
                ),
                category="activity",
                confidence=0.85,
            ),
        ),
        "low": (),
    },
    "hdl": {
        "low": (
            _Advice(
                title="More aerobic movement",
                action=(
                    "Regular aerobic activity is the most direct thing you can do about this one."
                ),
                mechanism="HDL responds to sustained aerobic work more than to any single food",
                category="activity",
                confidence=0.90,
            ),
            _Advice(
                title="Avocado, walnuts, almonds",
                action=(
                    "Avocado, walnuts and almonds are the food side, in the amounts you would "
                    "actually eat."
                ),
                mechanism=(
                    "monounsaturated fats are the dietary component associated with higher HDL"
                ),
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "triglycerides": {
        "high": (
            _Advice(
                title="Fewer sugary drinks",
                action=(
                    "Sugary drinks, alcohol and refined carbohydrates are where this usually comes "
                    "from."
                ),
                mechanism="the liver converts surplus simple sugar and alcohol into triglycerides",
                category="meal",
                confidence=0.90,
            ),
        ),
        "low": (),
    },
    "tsh": {
        "high": (
            _Advice(
                title="Worth raising with your doctor",
                action=(
                    "This is one to bring to whoever ordered the panel rather than to "
                    "change anything at the table."
                ),
                mechanism=(
                    "TSH is a pituitary signal about thyroid function, and there is no food that "
                    "reads it"
                ),
                category="habit",
                confidence=0.70,
            ),
        ),
        "low": (
            _Advice(
                title="Worth raising with your doctor",
                action=(
                    "This is one to bring to whoever ordered the panel rather than to "
                    "change anything at the table."
                ),
                mechanism=(
                    "TSH is a pituitary signal about thyroid function, and there is no food that "
                    "reads it"
                ),
                category="habit",
                confidence=0.70,
            ),
        ),
    },
    "magnesium": {
        "low": (
            _Advice(
                title="Pumpkin seeds, almonds, dark chocolate",
                action=(
                    "Pumpkin seeds, almonds, spinach and dark chocolate are the ones that fit into "
                    "a normal week."
                ),
                mechanism="those are the densest everyday food sources of magnesium",
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "zinc": {
        "low": (
            _Advice(
                title="Shellfish, seeds, chickpeas",
                action="Oysters, pumpkin seeds, beef and chickpeas are the practical sources.",
                mechanism="zinc concentrates in shellfish and seeds more than in most other foods",
                category="meal",
                confidence=0.80,
            ),
        ),
        "high": (),
    },
    "creatinine": {
        "high": (
            _Advice(
                title="Water, and a word with your doctor",
                action=(
                    "Drinking enough across the day is the one harmless thing to do here; "
                    "the reading itself is for whoever ordered the panel to interpret."
                ),
                mechanism=(
                    "how concentrated the blood is moves this number, so hydration is part of what "
                    "it reflects"
                ),
                category="habit",
                confidence=0.75,
            ),
        ),
        "low": (),
    },
}


def _band(panel: BloodPanel) -> str:
    """En qué banda de antigüedad cae *panel*: fresco, viejo, obsoleto o sin fecha.

    `age_days is None` cae en `_UNDATED` y **no** en fresco, que es la decisión de esta
    función y la que el docstring de `BloodPanel` dejó abierta: no saber de cuándo es pesa
    más parecido a viejo que a nuevo. El costo de advertir sobre un número de hace años es
    más alto que el de una tarjeta de menos, y acá no hay penalización que otra etapa
    pueda revertir — lo que se decide es si la app habla de salud o no.

    Sin fecha es su propia banda y no un alias de obsoleto porque lo que se le dice a la
    persona es distinto: un panel de hace dos años se repite, uno cuya fecha no se pudo
    leer se vuelve a subir. Y el techo mide una cosa que el parser puede equivocar: el
    `_extract_date` de `blood_analysis_parser` se queda con la **primera** fecha del
    documento, que puede ser la de nacimiento o la de impresión. Eso no lo arregla este
    punto; sí acota lo que la frescura puede prometer.
    """
    if panel.age_days is None:
        return _UNDATED
    if panel.age_days <= _FRESH_DAYS:
        return _FRESH
    if panel.age_days <= _OBSOLETE_DAYS:
        return _STALE
    return _OBSOLETE


def _age_phrase(age_days: int) -> str:
    """ "12 days ago" o "about 7 months ago" — contado, no elegido de una lista."""
    if age_days == 0:
        return "today"
    if age_days == 1:
        return "yesterday"
    if age_days < 60:
        return f"{age_days} days ago"
    months = round(age_days / 30)
    return f"about {months} months ago"


def _panel_phrase(panel: BloodPanel) -> str:
    """Cómo se nombra el panel dentro de una frase, con su fecha.

    La fecha va en ISO y no en `strftime("%d %b %Y")` porque el nombre del mes que
    `strftime` devuelve depende del locale del proceso, y esto corre en un job de fondo que
    no tiene locale de request — el mismo motivo por el que estas cadenas no pasan por
    `_()`. Una fecha ISO se lee igual desde cualquier parte y no inventa un vocabulario de
    doce meses que la i18n no podría tocar.
    """
    if panel.analysis_date is None or panel.age_days is None:
        return "your most recent panel"
    return f"your panel of {panel.analysis_date.isoformat()}, {_age_phrase(panel.age_days)}"


@dataclass(frozen=True)
class _Reading:
    """Un marcador fuera de rango, leído del blob una sola vez.

    Existe para que `_compose` reciba cuatro cosas y no siete, y para que el `.get` con
    default de cada campo del `values_json` esté en un solo lugar: el blob lo escribe el
    parser, así que `display_name` o `unit` pueden no estar y el generador no debería
    decidir eso dos veces.
    """

    key: str
    label: str
    status: str
    value: Any
    unit: str

    @property
    def is_low(self) -> bool:
        return self.status in _LOW_STATUSES

    @property
    def direction(self) -> str:
        return "below" if self.is_low else "above"

    @property
    def bucket(self) -> str:
        return "low" if self.is_low else "high"

    @property
    def reading(self) -> str:
        return f"{self.value} {self.unit}".strip()


def _compose(
    advice: _Advice,
    reading: _Reading,
    panel: BloodPanel,
    band: str,
) -> dict[str, Any]:
    """Una tarjeta entera a partir de lo único que la entrada sabe.

    Acá está la razón de que `_Advice` tenga tres campos y no un `text`: la observación, la
    fecha y el reparo por antigüedad son idénticos en las 22 tarjetas, y escritos dentro de
    cada una eran 22 lugares donde el encuadre podía divergir. El aviso de no-diagnóstico no
    se agrega acá sino en la pantalla, por lo que dice `_DISCLAIMER_TEMPLATES`: el `text`
    observa y propone, y quién puede leer el número lo dice la pantalla una vez.
    La división de campos es la de `explain.py`: `text` es la propuesta dirigida
    a la persona, `evidence_summary` el rastro con los números crudos, y `rationale`
    contesta por qué **esta** tarjeta y no otra — acá, qué marcador la disparó, con qué
    valor, de qué panel, y qué liga el alimento a ese marcador.
    """
    observation = (
        f"{reading.label} came back {reading.direction} its reference range on "
        f"{_panel_phrase(panel)}."
    )

    parts = [observation, advice.action]
    if band == _STALE:
        parts.append(
            "That panel is old enough that it may no longer describe you, so a fresh one "
            "would say more than this card can."
        )

    rationale = (
        f"{reading.label} read {reading.reading} on {_panel_phrase(panel)}, which is "
        f"{reading.direction} its reference range, and {advice.mechanism}."
    )
    if band == _STALE:
        rationale += (
            f" It ranks lower than it would on a fresh panel: past {_FRESH_DAYS} days the "
            "reading counts as the last thing measured, not as today's number."
        )

    confidence = advice.confidence
    if reading.status in _CRITICAL_STATUSES:
        confidence += _CRITICAL_CONFIDENCE_BUMP
    if band == _STALE:
        confidence *= _STALE_CONFIDENCE_FACTOR

    return {
        "title": advice.title,
        "text": " ".join(parts),
        "rationale": rationale,
        "category": advice.category,
        "confidence": round(min(1.0, confidence), 4),
        "source_type": "blood_analysis",
        #: El sujeto es el marcador, no el consejo. Cada marcador anormal produce
        #: hasta dos tarjetas con el mismo origen, así que anclarlas al alimento que
        #: nombran —"lentils", "spinach"— habría hecho que rechazar una enseñara sobre
        #: las lentejas cuando lo que la persona rechazó es que le hablen del hierro.
        #: Y con el marcador como sujeto, el dedup de la 4.4 alcanza para que un
        #: mismo panel no vuelva a producir la tarjeta que ya está pendiente.
        "subject_type": "biomarker",
        "subject_name": reading.key,
        "evidence_summary": (
            f"{reading.label}: {reading.reading} ({reading.status}) — "
            f"panel {panel.analysis_date.isoformat() if panel.analysis_date else 'undated'}"
        ),
    }


def _refresh_card(panel: BloodPanel, band: str, out_of_range: int) -> dict[str, Any]:
    """La única tarjeta que sale cuando el panel es demasiado viejo o no tiene fecha.

    Dice cuántos marcadores quedaron sin leer y no cuáles: el número es la razón para
    hacerse uno nuevo, y nombrarlos sería dar el consejo que esta rama existe para no dar.
    No lleva botón de acción —`web.actions.suggestion_action` excluye todo lo que venga de
    un panel— así que la pantalla se nombra en el texto, que es la forma que no le pone un
    "Anotar una comida" a un consejo de salud.
    """
    markers = f"{out_of_range} marker{'s' if out_of_range != 1 else ''}"
    if band == _UNDATED:
        title = "A panel with no date on it"
        text = (
            f"The most recent blood panel on file has no date the app could read, and "
            f"{markers} in it are outside their reference range. A value with no date "
            "could be from last month or from years ago, so nothing in it is being used "
            "to suggest food or activity. Uploading it again from Health with the date "
            "legible — or uploading a newer one — is what unblocks that."
        )
        rationale = (
            "There is no date for this panel, and an unknown age counts here as old "
            "rather than as new: advising off a number that might be years old costs "
            f"more than one card less. {markers} outside range went unread for that reason."
        )
    else:
        title = "Time for a new panel"
        text = (
            f"The most recent blood panel on file is {_panel_phrase(panel)}, and {markers} "
            "in it are outside their reference range. Past a year those values describe "
            "that day more than they describe you, so none of them is being used to "
            "suggest food or activity. A new panel, uploaded from Health, brings this "
            "part of the app back."
        )
        rationale = (
            f"The panel is {_age_phrase(panel.age_days or 0)}, past the {_OBSOLETE_DAYS}-day "
            f"ceiling this generator stops advising at, so its {markers} outside range "
            "produced nothing."
        )

    return {
        "title": title,
        "text": text,
        "rationale": rationale,
        "category": "habit",
        "confidence": _REFRESH_CONFIDENCE,
        "source_type": "blood_analysis",
        "subject_type": "habit",
        "subject_name": _REFRESH_SUBJECT,
        "evidence_summary": (
            f"Latest panel: "
            f"{panel.analysis_date.isoformat() if panel.analysis_date else 'no date read'}"
            f"; {out_of_range} marker(s) outside range, none used for advice"
        ),
    }


def generate(
    user: User,
    panel: BloodPanel | None,
) -> list[dict[str, Any]]:
    """Generate suggestions based on abnormal blood biomarker values.

    Args:
        user: The target User.
        panel: El último panel legible de esa persona, con su fecha, o `None` si no hay.

    Returns:
        List of candidate suggestion dicts with title, text, rationale, category, confidence.

    Recibía `db` —declarado "unused currently, reserved for future DB lookups"— y un dict de
    marcadores sin fecha, porque `get_latest_values` devolvía el blob y tiraba la fila. Ahora
    recibe el panel entero: la antigüedad estaba en la base todo este tiempo y era lo único
    que faltaba para no dar consejos sobre un análisis de hace tres años.

    Un panel obsoleto o sin fecha con **todo** normal no produce ni la tarjeta de repetirlo:
    la razón para hacerse uno nuevo es que había algo que se dejó sin leer, y sin eso el
    aviso sería una nota al pie con forma de alarma.
    """
    if panel is None or not panel.values:
        return []

    band = _band(panel)
    abnormal = [
        _Reading(
            key=key,
            label=str(data.get("display_name") or key),
            status=data["status"],
            value=data.get("value"),
            unit=str(data.get("unit") or ""),
        )
        for key, data in panel.values.items()
        if data.get("status", "normal") in _ACTIONABLE_STATUSES
    ]

    if band in (_OBSOLETE, _UNDATED):
        if not abnormal:
            return []
        logger.info(
            "Blood generator withheld advice for user_id=%d: panel band=%s, %d abnormal marker(s).",
            user.id,
            band,
            len(abnormal),
        )
        return [_refresh_card(panel, band, len(abnormal))]

    candidates = [
        _compose(advice, reading, panel, band)
        for reading in abnormal
        for advice in _BIOMARKER_ADVICE.get(reading.key, {}).get(reading.bucket, ())
    ]

    logger.debug(
        "Blood generator produced %d candidates for user_id=%d (panel band=%s)",
        len(candidates),
        user.id,
        band,
    )
    return candidates
