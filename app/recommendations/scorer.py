"""Preference-based scoring and ranking for recommendation candidates.

Scoring model (additive):
- Base score: candidate's own confidence value
- The candidate's **subject** is looked up in what the person has taught the app: the
  boost or penalty is the *direction* of what was learned (the average of the signals)
  scaled by how much evidence backs it (`learning.SubjectAffinity`)
- Y lo mismo con el **atributo** del sujeto —la categoría del alimento—, descontando lo
  que el sujeto mismo aportó y valiendo la mitad: es lo que permite acertar con algo que
  la persona nunca vio, sin que una categoría pese como una opinión sobre el plato
- Y si el candidato declara la **franja horaria** para la que se ofrece, cuánto mejor le
  cae ese sujeto a esa hora que al resto de las horas: es lo que separa el café del
  desayuno del café de la cena, que hasta acá eran el mismo número
- Y al final, lo único que no es un gusto: **cuánto de eso ya hubo hace muy poco**. Las
  mismas señales leídas con un reloj catorce veces más corto, que siempre resta — es lo que
  separa "me gusta" de "lo comí ayer", que hasta acá eran la misma suma
- A subject already suggested in the last few days receives a diversity penalty
- Scores are clamped to [0.0, 1.0]

The result list is sorted by score descending.

Hasta la 4.4 el match era por bolsa de palabras: se tokenizaban `title + text +
rationale` del candidato y `entity_name` de la señal —que era el **título renderizado** de
la sugerencia respondida—, y con 30% de solape ya había ajuste. Eso hacía que rechazar
*"Time to get moving!"* penalizara cualquier candidato que compartiera "moving", "boost" o
"energy", cruzando categorías: una sugerencia de comida bajaba de score por un rechazo de
actividad. Ahora la comparación es la igualdad del sujeto normalizado, y el vocabulario
—qué es un sujeto, qué señales cuentan— vive en `app/recommendations/learning.py`, del
mismo lado que las escrituras.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.clock import as_utc
from app.models.signal import BehaviorSignal
from app.models.suggestion import Suggestion
from app.models.user import User
from app.recommendations import learning

logger = logging.getLogger(__name__)

# Tuning knobs
_POSITIVE_SIGNAL_BOOST = 0.12
_NEGATIVE_SIGNAL_PENALTY = 0.15
_DIVERSITY_PENALTY = 0.20  # per subject already suggested recently
_RECENT_SUGGESTION_DAYS = 7  # window for diversity check

#: La ventana de señales ya no se decide acá. Era `30` escrito a mano en este módulo **y**
#: en `engine.py`, y desde la 4.4.2 lo que decide cuánto pesa una señal vieja es su
#: semivida, no un corte: el horizonte solo acota la consulta y lo declara `learning`, del
#: mismo lado que las semividas de las que se deriva.
_RECENT_SIGNAL_DAYS = learning.SIGNAL_HORIZON_DAYS

#: Cuánto vale una generalización comparada con la evidencia directa: la mitad, siempre.
#: La vara de evidencia más alta del nivel atributo (`learning`) decide *cuándo* se le
#: cree; esto decide *cuánto* se le cree una vez que se le cree, y hace falta además
#: porque la confianza satura hacia 1: con cien verduras registradas —cosa que pasa en
#: meses, no en años— una verdura que la persona nunca comió llegaría al mismo ajuste que
#: su comida favorita. Una categoría es una de las razones por las que algo gusta, nunca
#: la razón entera.
_ATTRIBUTE_SIGNAL_SCALE = 0.5

#: Cuánto puede bajar un candidato por haberse consumido hace poco. Es el mismo número que
#: la penalización de un "no", y no por simetría estética: tiene que poder **cancelar** el
#: boost acumulado de un favorito, cuyo techo es `_POSITIVE_SIGNAL_BOOST`, o el bucle sigue
#: empujando a repetir. Y no más que eso: un favorito comido ayer queda debajo de una
#: alternativa igual de querida y por encima de cero — deja de aparecer tres días seguidos,
#: no deja de ser un favorito.
#:
#: La consecuencia que conviene tener a la vista: para señales del mismo día la saciedad y
#: el boost puntual crecen con la misma curva, así que la resta queda en `-0.03 * presión`.
#: Comer algo hoy lo hace, hoy, un poco menos sugerible — y en una semana la saciedad se
#: apagó y queda solo la afinidad, que es exactamente el reparto que pedía la 4.4.6.
_SATIETY_PENALTY = 0.15


def _learned_delta(strength: float) -> float:
    """El ajuste que le corresponde a una fuerza aprendida, con la perilla de su signo."""
    knob = _POSITIVE_SIGNAL_BOOST if strength > 0 else _NEGATIVE_SIGNAL_PENALTY
    return knob * strength


def score_candidates(
    candidates: list[dict[str, Any]],
    user: User,
    signals: list[BehaviorSignal],
    recent_suggestions: list[Suggestion],
    *,
    subject_attributes: Mapping[tuple[str, str], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Score and rank recommendation candidates.

    Args:
        candidates: List of suggestion dicts (from generators, post-filter).
        user: The User model instance.
        signals: Recent BehaviorSignal rows for the user.
        recent_suggestions: Recent Suggestion rows for context (diversity).
        subject_attributes: A qué atributo generaliza cada sujeto puntual
            (`learning.attribute_index`). Es opcional y llega por keyword porque el
            scorer no toca la base: quien tiene sesión —el motor— arma el índice y lo
            pasa, y sin él el nivel atributo simplemente no aporta nada.

    Returns:
        The same list, each dict augmented with a "_score" key, sorted
        by _score descending.

    Un candidato sin sujeto queda con su confianza cruda: no recibe boost, ni penalización,
    ni castigo por repetición. Es la consecuencia buscada de no tener fallback al título
    (ver `learning.candidate_subject`); que ningún generador se olvide de declararlo lo
    cuida un test que los recorre, no una rama acá.
    """
    if not candidates:
        return []

    cutoff_signals = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SIGNAL_DAYS)
    cutoff_suggestions = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SUGGESTION_DAYS)

    # Filter signals to recent window
    relevant_signals = [
        s for s in signals if s.created_at is None or as_utc(s.created_at) >= cutoff_signals
    ]
    affinity = learning.subject_affinities(relevant_signals)

    #: Lo mismo, un nivel más arriba: lo que la app aprendió de cada **categoría**. Se
    #: calcula acá y no por candidato porque son las mismas señales agrupadas de otra
    #: forma, y hay hasta cinco candidatos por corrida contra cientos de señales.
    attributes = subject_attributes or {}
    by_attribute = learning.attribute_affinities(relevant_signals, attributes)

    #: Y las mismas señales partidas por hora, para el eje temporal. No lleva perilla de
    #: escala propia —a diferencia del atributo— porque una franja no es una
    #: generalización sino un recorte más específico: no hay razón para creerle
    #: sistemáticamente menos, y lo que sabe de menos ya se lo descuenta su propia
    #: confianza, que con menos observaciones es más baja.
    by_slot = learning.slot_affinities(relevant_signals)

    #: Y las mismas señales una vez más, leídas como consumo reciente en vez de como gusto.
    #: Es el único de los cuatro cómputos que no responde "¿le gusta?" sino "¿cuánto ya
    #: hubo?", y el único que solo puede restar.
    satiety = learning.satiety_pressure(relevant_signals)

    #: Los sujetos ya sugeridos hace poco, para no repetirlos. Antes esto era un conjunto
    #: de títulos, y las dos comparaciones —exacta y por solape de tokens— fallaban del
    #: mismo modo: "Use your milk today" y "Use your last milk" son la misma sugerencia
    #: con dos redacciones, y no se reconocían entre sí.
    recent_subjects: set[tuple[str, str]] = set()
    for suggestion in recent_suggestions:
        if suggestion.created_at is not None and as_utc(suggestion.created_at) < cutoff_suggestions:
            continue
        if suggestion.subject_type and suggestion.subject_name:
            recent_subjects.add(
                learning.subject_key(suggestion.subject_type, suggestion.subject_name)
            )

    scored: list[dict[str, Any]] = []

    for candidate in candidates:
        base_score = float(candidate.get("confidence", 0.5))
        subject = learning.candidate_subject(candidate)
        adjustment = 0.0

        if subject is not None:
            learned = affinity.get(subject)
            #: `strength` ya viene acotado a `[-1, 1]` y ponderado por cuánta evidencia lo
            #: sostiene (4.4.3), así que acá no hace falta ningún tope: antes era
            #: `min(suma, 1.0)`, un recorte puesto para que la suma no se desbordara —cada
            #: comida registrada escribe un `repeated_meal_choice` de 1.0— y que de paso
            #: hacía que un único tap moviera el score igual que diez observaciones
            #: consistentes. Repartir lo positivo entre afinidad estable y saciedad
            #: reciente sigue siendo la 4.4.6.
            strength = learned.strength if learned is not None else 0.0
            if strength:
                delta = _learned_delta(strength)
                adjustment += delta
                logger.debug(
                    "Candidate %r: %+.3f from subject %s (dirección=%+.2f, evidencia=%.2f)",
                    candidate.get("title"),
                    delta,
                    subject,
                    learned.direction if learned else 0.0,
                    learned.evidence if learned else 0.0,
                )

            #: El segundo nivel: lo que la app sabe de la categoría del candidato, sin
            #: contar al candidato mismo. Es lo único que puede mover una espinaca que
            #: la persona nunca vio en una sugerencia, y por eso se suma **además** del
            #: ajuste puntual en vez de reemplazarlo cuando este es cero: un alimento con
            #: una señal propia también hereda algo de su categoría, solo que ahí la
            #: evidencia propia es la que manda.
            generalized = learning.generalized_affinity(
                subject, attributes=attributes, points=affinity, groups=by_attribute
            )
            if generalized is not None and generalized.strength:
                delta = _learned_delta(generalized.strength) * _ATTRIBUTE_SIGNAL_SCALE
                adjustment += delta
                logger.debug(
                    "Candidate %r: %+.3f from attribute %s (dirección=%+.2f, evidencia=%.2f)",
                    candidate.get("title"),
                    delta,
                    attributes.get(subject),
                    generalized.direction,
                    generalized.evidence,
                )

            #: El tercer eje: la hora. Solo participan los candidatos que declaran para
            #: qué franja se ofrecen —hoy los de comida—, y solo mueve el score si el
            #: sujeto se comporta distinto a esa hora que al resto. Como los otros dos, no
            #: filtra: que nunca se haya registrado un café a la cena no es un "no".
            slot = learning.candidate_slot(candidate)
            if slot is not None:
                contrast = learning.slot_contrast(subject, slot, slots=by_slot)
                if contrast:
                    delta = _learned_delta(contrast)
                    adjustment += delta
                    logger.debug(
                        "Candidate %r: %+.3f from slot %s (contraste=%+.2f)",
                        candidate.get("title"),
                        delta,
                        slot,
                        contrast,
                    )

            #: Y lo que no es un gusto: cuánto de esto ya hubo hace muy poco. Nunca suma, y
            #: no es lo mismo que la penalización por diversidad de abajo: esa mira lo que
            #: la app **sugirió** —y es un escalón fijo por siete días—, esto mira lo que la
            #: persona **hizo** y se apaga solo en un par de días. Tampoco filtra: haber
            #: comido milanesas ayer no es un "no" a las milanesas.
            pressure = satiety.get(subject, 0.0)
            if pressure:
                delta = -_SATIETY_PENALTY * pressure
                adjustment += delta
                logger.debug(
                    "Candidate %r: %+.3f from satiety (presión=%.2f)",
                    candidate.get("title"),
                    delta,
                    pressure,
                )

            if subject in recent_subjects:
                adjustment -= _DIVERSITY_PENALTY
                logger.debug(
                    "Candidate %r: -%.2f diversity penalty (subject %s already suggested).",
                    candidate.get("title"),
                    _DIVERSITY_PENALTY,
                    subject,
                )

        final_score = max(0.0, min(1.0, base_score + adjustment))
        scored_candidate = {**candidate, "_score": round(final_score, 4)}
        scored.append(scored_candidate)

    scored.sort(key=lambda c: c["_score"], reverse=True)
    return scored
