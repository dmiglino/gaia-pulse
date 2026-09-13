"""Preference-based scoring and ranking for recommendation candidates.

Scoring model (additive):
- Base score: candidate's own confidence value
- The candidate's **subject** is looked up in what the person has taught the app: the
  boost or penalty is the *direction* of what was learned (the average of the signals)
  scaled by how much evidence backs it (`learning.SubjectAffinity`)
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


def score_candidates(
    candidates: list[dict[str, Any]],
    user: User,
    signals: list[BehaviorSignal],
    recent_suggestions: list[Suggestion],
) -> list[dict[str, Any]]:
    """Score and rank recommendation candidates.

    Args:
        candidates: List of suggestion dicts (from generators, post-filter).
        user: The User model instance.
        signals: Recent BehaviorSignal rows for the user.
        recent_suggestions: Recent Suggestion rows for context (diversity).

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
                knob = _POSITIVE_SIGNAL_BOOST if strength > 0 else _NEGATIVE_SIGNAL_PENALTY
                delta = knob * strength
                adjustment += delta
                logger.debug(
                    "Candidate %r: %+.3f from subject %s (dirección=%+.2f, evidencia=%.2f)",
                    candidate.get("title"),
                    delta,
                    subject,
                    learned.direction if learned else 0.0,
                    learned.evidence if learned else 0.0,
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
