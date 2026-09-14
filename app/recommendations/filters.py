"""Hard constraint filtering for recommendations.

Removes candidates that violate explicit user constraints:
- Impossible activities (e.g. user can't swim due to injury)
- Disliked/avoided foods
- Strong negative RecommendationPreference entries

These filters are applied BEFORE scoring — candidates that fail are
completely removed, not just penalised.

Y por eso la regla de la duda: un candidato cuya categoría **no** se pudo determinar se
compara contra los dos conjuntos de bloqueos en vez de contra ninguno (ver
`_sides_to_check`). Acá no hay penalización que se pueda revertir más adelante —el candidato
desaparece antes de tener score—, así que el error caro no es descartar una tarjeta de más
sino dejar pasar la que alguien declaró que no puede.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations import learning

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HouseholdMember:
    """Una persona de la casa con lo suyo, y solo lo suyo.

    Existe para que las sugerencias de scope household se puedan filtrar sin perder de
    vista de quién es cada dato. Las tres piezas se leen por `user_id` —regla 4 de
    `AGENTS.md`— y viajan juntas justamente para que no se puedan mezclar: un conjunto
    único de señales "de la casa" no se podría desarmar después, y todo el punto de la
    4.4.9 es que las dos mitades se traten distinto.
    """

    user: User
    preferences: list[RecommendationPreference] = field(default_factory=list)
    signals: list[BehaviorSignal] = field(default_factory=list)


#: Las señales de preferencia que **bloquean**, una sola vez. Estaban escritas dos veces
#: —`("dislikes", "impossible", "avoid")` para comida y las mismas tres en otro orden para
#: actividad—, que es la forma de duplicación que más cara sale: dos listas que tienen que
#: coincidir y que nada obliga a coincidir, así que agregar una señal a una y no a la otra
#: no rompe ningún test, solo deja de bloquear del lado que se olvidó.
#: `possible_sometimes` y `preferred` quedan afuera a propósito: no son un "no".
_BLOCKING_SIGNALS = frozenset({"dislikes", "impossible", "avoid"})

#: Los `item_type` que cuentan como comida. `exercise` es el único del lado de actividad,
#: así que ahí no hace falta un conjunto.
_FOOD_PREFERENCE_TYPES = frozenset({"food", "ingredient", "recipe", "cuisine"})


def _normalise(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _any_token_matches(candidate_text: str, blocked_terms: set[str]) -> bool:
    """Return True if any blocked term appears as a substring in the candidate text."""
    norm = _normalise(candidate_text)
    return any(term and term in norm for term in blocked_terms)


def _build_blocked_set(
    user: User,
    preferences: list[RecommendationPreference],
    constraint_type: str,
) -> set[str]:
    """Build a set of normalised blocked terms for a given constraint type.

    constraint_type: "food" | "activity"
    """
    blocked: set[str] = set()

    if constraint_type == "food":
        for lst in (
            user.disliked_foods_json or [],
            user.dietary_restrictions_json or [],
        ):
            for item in lst:
                blocked.add(_normalise(item))

        for pref in preferences:
            if (
                pref.item_type in _FOOD_PREFERENCE_TYPES
                and pref.preference_signal in _BLOCKING_SIGNALS
            ):
                blocked.add(_normalise(pref.item_name))

    elif constraint_type == "activity":
        for lst in (
            user.impossible_activities_json or [],
            user.disliked_activities_json or [],
        ):
            for item in lst:
                blocked.add(_normalise(item))

        for pref in preferences:
            if pref.item_type == "exercise" and pref.preference_signal in _BLOCKING_SIGNALS:
                blocked.add(_normalise(pref.item_name))

    return blocked


#: Las categorías que cada lado reconoce. Declaradas y no inferidas del nombre, porque
#: `recovery` es actividad y `pantry` es comida sin que ninguna de las dos palabras lo diga.
_FOOD_CATEGORIES = frozenset({"meal", "shopping", "pantry"})
_ACTIVITY_CATEGORIES = frozenset({"activity", "workout", "exercise", "recovery"})

_FOOD_WORDS = ("eat", "food", "meal", "recipe", "cook", "drink", "pantry")
_ACTIVITY_WORDS = ("workout", "gym", "run", "swim", "bike", "yoga", "exercise")

#: Qué lados mira un candidato del que no se pudo decidir la categoría: **los dos**. Ver
#: `_sides_to_check`.
_BOTH_SIDES = ("food", "activity")


def _sides_to_check(candidate: dict[str, Any]) -> tuple[str, ...]:
    """Contra qué conjuntos de bloqueos se compara *candidate*: comida, actividad, o los dos.

    Antes esto era `_infer_category`, devolvía `str | None`, y el `None` significaba en la
    práctica **no chequear nada**: `cat == "food" and …` y `cat == "activity" and …` son las dos
    únicas ramas de descarte, así que una categoría desconocida pasaba entera sin comparar
    contra un solo bloqueo. El alcance real de eso son las tres tarjetas de sangre con
    `category="habit"` —TSH alta, TSH baja, creatinina alta—, que son justo las que más
    conviene que atraviesen los filtros como todas las demás.

    Ahora un desconocido mira los dos conjuntos. El costo es más falsos positivos, porque
    `_any_token_matches` compara por substring: si alguien bloqueó "run", una tarjeta de
    comida cuyo texto diga "runny honey" se descarta. Se acepta **en esa dirección a
    propósito**: mostrar una tarjeta de menos es preferible a mostrarle carne a quien declaró
    que no come carne, y el candidato desaparece en silencio de una lista que igual se rearma
    en la próxima corrida.

    Que un lado no chequee lo del otro cuando la categoría **sí** se conoce no es una
    inconsistencia: es lo que evita que un ejercicio llamado "burpee" caiga por un alimento
    bloqueado que se le parezca. La ampliación es para lo que no se pudo clasificar, no para
    todo.
    """
    cat = candidate.get("category", "").lower()
    if cat in _FOOD_CATEGORIES:
        return ("food",)
    if cat in _ACTIVITY_CATEGORIES:
        return ("activity",)

    # Fall back to scanning text
    text = (candidate.get("title", "") + " " + candidate.get("text", "")).lower()
    if any(w in text for w in _FOOD_WORDS):
        return ("food",)
    if any(w in text for w in _ACTIVITY_WORDS):
        return ("activity",)
    return _BOTH_SIDES


def apply_signal_constraints(
    candidates: list[dict[str, Any]],
    signals: list[Any],
) -> list[dict[str, Any]]:
    """Remove candidates whose subject carries enough live negative weight.

    This is a hard pre-filter (complements the scorer's penalty): if the user has
    clearly said no to a subject, don't show it again regardless of how scoring
    would rank it.

    Desde la 4.4.10 el criterio es una **suma** de peso negativo vivo y no "hay un
    negativo": lo que decide qué cuenta y cuánto es `learning.rejected_subjects`, no esta
    función. En la práctica sigue significando "lo rechazó a propósito y hace poco" —una
    ausencia inferida no junta peso suficiente para llegar acá, ver
    `learning._FILTER_EVIDENCE_FLOOR`—, pero el nombre del criterio ya no es "explícito".

    Hasta la 4.4 esto comparaba tokens: se tomaba `entity_name` de la señal —el título
    renderizado de la sugerencia rechazada— y se lo cruzaba contra `title + text` del
    candidato, borrándolo con 60% de solape. Con títulos de una frase el umbral se
    alcanzaba por accidente y en la dirección peor posible, porque acá no hay penalización
    que se pueda revertir: el candidato desaparece antes de tener score. Ahora hace falta
    que el sujeto sea el mismo, y qué señales cuentan como "no" lo decide
    `learning.rejected_subjects`, no un `value < 0` acá —que es lo que hacía que un
    "más tarde" (`value=-0.3` en `dismissed`) borrara la sugerencia como si fuera un
    rechazo.

    Args:
        candidates: Filtered candidate dicts from apply_hard_constraints.
        signals: Recent BehaviorSignal rows (pre-filtered to relevant window).

    Returns:
        Candidates with explicitly-rejected subjects removed.
    """
    rejected = learning.rejected_subjects(signals)
    if not rejected:
        return candidates

    kept: list[dict[str, Any]] = []
    removed = 0
    for candidate in candidates:
        subject = learning.candidate_subject(candidate)
        if subject is not None and subject in rejected:
            logger.debug(
                "Signal-constrained out candidate %r (rejected subject %s).",
                candidate.get("title"),
                subject,
            )
            removed += 1
            continue
        kept.append(candidate)

    if removed:
        logger.info("Signal constraint filter removed %d candidate(s).", removed)
    return kept


def apply_hard_constraints(
    candidates: list[dict[str, Any]],
    user: User,
    preferences: list[RecommendationPreference],
) -> list[dict[str, Any]]:
    """Remove candidates that violate hard constraints.

    Args:
        candidates: Raw list of suggestion dicts from generators.
        user: The User model instance for this recommendation context.
        preferences: All RecommendationPreference rows for the user.

    Returns:
        Filtered list with constraint-violating candidates removed.
    """
    return _drop_blocked(
        candidates,
        _build_blocked_set(user, preferences, "food"),
        _build_blocked_set(user, preferences, "activity"),
    )


def _drop_blocked(
    candidates: list[dict[str, Any]],
    blocked_food: set[str],
    blocked_activity: set[str],
) -> list[dict[str, Any]]:
    """El descarte en sí, separado de cómo se armaron los conjuntos.

    Lo usan los dos caminos —el personal y el de la casa— y por eso está acá: la única
    diferencia entre ellos es de dónde salen `blocked_food` y `blocked_activity`, no cómo
    se comparan. Dos copias de esta comparación es cómo un bloqueo empieza a valer en una
    pantalla y no en la otra.

    Acá había un `if not blocked_food and not blocked_activity: return candidates` que era
    **preservador de conducta por construcción** —con los dos conjuntos vacíos las
    comparaciones de abajo no pueden descartar nada— y esa es exactamente la razón para
    borrarlo: es una trampa, no un agujero. El día que este filtro tenga que mirar algo que no
    sean esos dos conjuntos —una restricción del hogar, un umbral del contexto— el atajo lo
    saltea en silencio y con la suite en verde. Cuatro comparaciones no valen un modo de falla
    silencioso.
    """
    kept: list[dict[str, Any]] = []
    removed = 0
    blocked_by_side = {"food": blocked_food, "activity": blocked_activity}

    for candidate in candidates:
        full_text = _normalise(candidate.get("title", "") + " " + candidate.get("text", ""))
        #: Cuál de los lados lo bloqueó, y no solo si alguno: es lo que hace que el log diga
        #: por qué desapareció una tarjeta, que en un filtro que borra sin dejar rastro en la
        #: UI es la única forma de auditarlo.
        blocking_side = next(
            (
                side
                for side in _sides_to_check(candidate)
                if _any_token_matches(full_text, blocked_by_side[side])
            ),
            None,
        )
        if blocking_side is not None:
            logger.debug("Filtered out %s suggestion: %r", blocking_side, candidate.get("title"))
            removed += 1
            continue

        kept.append(candidate)

    if removed:
        logger.info("Hard constraint filter removed %d candidate(s).", removed)

    return kept


def apply_household_constraints(
    candidates: list[dict[str, Any]],
    members: Sequence[HouseholdMember],
) -> list[dict[str, Any]]:
    """Filtrar candidatos de la casa contra sus dos personas — cada regla a su manera.

    Hasta acá `generate_for_household` no filtraba **nada**: el comentario decía "for
    household suggestions we skip user-specific filtering" y era literal. Como el generador
    de despensa propone alimentos concretos (`subject_type="food"`), eso significaba que la
    lista de compras podía traer justo lo que una de las dos personas no puede comer. Con
    la 4.5 esa función pasa a tener llamadores; conviene que cuando se prenda ya no lo haga.

    Las dos reglas van al revés a propósito, y esa asimetría es todo el punto:

    - **Los bloqueos duros se unen.** Si a Diego el maní le hace mal, la casa no compra
      maní: alcanza que **una** persona lo tenga bloqueado. Una restricción declarada no
      pide evidencia ni admite promedio, y el costo de equivocarse no es simétrico —una
      compra de más contra una comida que alguien no puede comer—.
    - **Los "no" aprendidos se intersectan.** Un rechazo de conducta de una sola persona no
      es un "no" de la casa: en una casa de dos, unirlos dejaría que un rechazo de Rocío
      borre de la lista de compras el alimento que Diego come todos los días. Solo se saca
      un sujeto si **todas** las personas lo rechazaron, y hasta entonces sigue compitiendo
      —más abajo si corresponde, que es trabajo del score y no de este filtro—.

    Y ninguna de las dos mira una tabla "de la casa": los datos entran ya separados por
    persona en `HouseholdMember`, porque una intersección solo se puede calcular sobre
    conjuntos que nunca se mezclaron.

    Args:
        candidates: Candidatos crudos del generador de despensa.
        members: Las personas de la casa, cada una con sus preferencias y señales.

    Returns:
        Los candidatos que ninguna persona bloquea y que no rechazaron todas.
    """
    if not members:
        #: Una casa sin miembros no debería existir, y si existiera la intersección de cero
        #: conjuntos sería "todo rechazado": el filtro borraría la lista entera. Devolver
        #: los candidatos tal cual es la falla segura —no hay nadie de quien proteger a
        #: nadie— y el warning es para que no pase inadvertido.
        logger.warning("Household constraint filter got no members — nothing to check against.")
        return candidates

    blocked_food: set[str] = set()
    blocked_activity: set[str] = set()
    for member in members:
        blocked_food |= _build_blocked_set(member.user, member.preferences, "food")
        blocked_activity |= _build_blocked_set(member.user, member.preferences, "activity")

    kept = _drop_blocked(candidates, blocked_food, blocked_activity)

    rejected_by_all = set.intersection(
        *(learning.rejected_subjects(member.signals) for member in members)
    )
    if not rejected_by_all:
        return kept

    survivors: list[dict[str, Any]] = []
    removed = 0
    for candidate in kept:
        subject = learning.candidate_subject(candidate)
        if subject is not None and subject in rejected_by_all:
            logger.debug(
                "Household filter dropped %r: subject %s rejected by every member.",
                candidate.get("title"),
                subject,
            )
            removed += 1
            continue
        survivors.append(candidate)

    if removed:
        logger.info(
            "Household constraint filter removed %d candidate(s) rejected by all %d member(s).",
            removed,
            len(members),
        )
    return survivors
