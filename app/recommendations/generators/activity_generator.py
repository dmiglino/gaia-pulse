"""Activity / workout suggestion generator.

Generates activity suggestions for a user based on:
- Days since last workout (rest vs. activity nudge)
- Recent workout history (avoid over-training same muscle groups)
- User's preferred, disliked, and impossible activities
- Explicit RecommendationPreference entries
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations.context import UserContext

logger = logging.getLogger(__name__)

_REST_DAY_THRESHOLD = 3   # Suggest workout if inactive for this many days
_OVERTRAINING_DAYS = 2    # Avoid same muscle group within this window
_MAX_SUGGESTIONS = 6

# Muscle group recovery time (days) for simple rules
_MUSCLE_RECOVERY: dict[str, int] = {
    "chest": 2,
    "shoulders": 2,
    "triceps": 2,
    "biceps": 2,
    "back": 2,
    "legs": 3,
    "core": 1,
    "cardio": 1,
}

# Default activity catalog (used when user has no preference data)
_DEFAULT_ACTIVITIES: list[dict[str, Any]] = [
    {"name": "walking", "type": "outdoor", "intensity": "low"},
    {"name": "biking", "type": "outdoor", "intensity": "moderate"},
    {"name": "yoga", "type": "home", "intensity": "low"},
    {"name": "gym", "type": "gym", "intensity": "high"},
    {"name": "running", "type": "outdoor", "intensity": "high"},
    {"name": "swimming", "type": "outdoor", "intensity": "moderate"},
    {"name": "pilates", "type": "home", "intensity": "moderate"},
    {"name": "hiit", "type": "home", "intensity": "high"},
]


def _recently_trained_muscles(context: UserContext, days: int = _OVERTRAINING_DAYS) -> set[str]:
    """Los grupos musculares estimulados dentro de los últimos *days* días.

    Acá había una consulta —y `_days_since_last_workout` era otra— que preguntaba lo mismo
    que el contexto ya trae. El corte es `< days` y no `<= days` porque eso es lo que hacía
    el `WHERE timestamp >= ahora − days` que reemplaza: los días se truncan, así que un
    estímulo de hace 2.5 días daba `days_since == 2` y quedaba **fuera** de una ventana de 2.

    Sigue siendo un conjunto de nombres, y no la ventana de recuperación por grupo que pide
    la 4.5.2: ese cambio es de conducta y va en su punto, no de contrabando acá.
    """
    return {
        group for group, since in context.days_since_muscle_group.items() if since < days
    }


def _get_impossible_activities(
    user: User, preferences: list[RecommendationPreference]
) -> set[str]:
    impossible: set[str] = set(a.lower() for a in (user.impossible_activities_json or []))
    for pref in preferences:
        if pref.item_type == "exercise" and pref.preference_signal == "impossible":
            impossible.add(pref.item_name.lower())
    return impossible


def _get_disliked_activities(
    user: User, preferences: list[RecommendationPreference]
) -> set[str]:
    disliked: set[str] = set(a.lower() for a in (user.disliked_activities_json or []))
    for pref in preferences:
        if pref.item_type == "exercise" and pref.preference_signal in ("dislikes", "avoid"):
            disliked.add(pref.item_name.lower())
    return disliked


def _get_preferred_activities(
    user: User, preferences: list[RecommendationPreference]
) -> list[str]:
    preferred = list(a.lower() for a in (user.preferred_activities_json or []))
    for pref in preferences:
        if pref.item_type == "exercise" and pref.preference_signal in ("likes", "preferred"):
            if pref.item_name.lower() not in preferred:
                preferred.append(pref.item_name.lower())
    return preferred


def generate(
    user: User,
    preferences: list[RecommendationPreference],
    context: UserContext,
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate activity suggestions for *user*.

    Returns a list of suggestion dicts.

    Sin `db`: todo lo que este generador leía de la base lo trae el contexto, así que pedir
    una sesión sería pedir permiso para volver a consultar por su cuenta.
    """
    suggestions: list[dict[str, Any]] = []

    days_since = context.days_since_last_workout
    recent_muscles = _recently_trained_muscles(context)
    impossible = _get_impossible_activities(user, preferences)
    disliked = _get_disliked_activities(user, preferences)
    preferred = _get_preferred_activities(user, preferences)

    # ── 1. Rest nudge: if trained yesterday / same day ─────────────────────
    if days_since is not None and days_since == 0:
        suggestions.append(
            {
                "category": "activity",
                #: Un hábito, no un ejercicio: esta tarjeta no propone una actividad,
                #: propone no hacer ninguna. Con `subject_type="exercise"` un rechazo acá
                #: habría enseñado que no le gusta el descanso *como ejercicio*, y el
                #: sujeto "rest" habría chocado con un ejercicio que se llamara igual.
                "subject_type": "habit",
                "subject_name": "rest day",
                "title": "Consider a rest or light activity today",
                "text": (
                    "You already worked out today. A short walk or yoga session can support "
                    "recovery without over-training."
                ),
                "rationale": "Adequate rest is essential for muscle repair and performance.",
                "evidence_summary": "Workout logged today.",
                "confidence": 0.75,
                "source_type": "rule",
            }
        )

    # ── 2. Activity nudge: if been resting for too long ────────────────────
    if days_since is None or days_since >= _REST_DAY_THRESHOLD:
        reason = "No workouts logged yet" if days_since is None else f"{days_since} days since last workout"
        suggestions.append(
            {
                "category": "activity",
                #: El candidato que le da nombre al bug: rechazar *"Time to get moving!"*
                #: guardaba la frase entera como entidad aprendida, y "moving" y "boost"
                #: y "energy" alcanzaban para penalizar o filtrar sugerencias de comida.
                #: Su sujeto real es la constancia, y rechazarlo enseña una sola cosa:
                #: esta persona no quiere que la empujen a entrenar.
                "subject_type": "habit",
                "subject_name": "workout consistency",
                "title": "Time to get moving!",
                "text": (
                    "It's been a few days since your last workout. "
                    "Even a 30-minute session can boost your mood and energy."
                ),
                "rationale": "Consistency is key for fitness — regular activity supports long-term wellness.",
                "evidence_summary": reason + ".",
                "confidence": 0.8,
                "source_type": "rule",
            }
        )

    # ── 3. Muscle group rotation: suggest what hasn't been trained ─────────
    all_muscle_groups = set(_MUSCLE_RECOVERY.keys())
    rested_muscles = all_muscle_groups - recent_muscles
    if rested_muscles:
        example = sorted(rested_muscles)[0]
        suggestions.append(
            {
                "category": "activity",
                "subject_type": "muscle_group",
                "subject_name": example,
                "title": f"Train {example} today",
                "text": (
                    f"Your {example} muscles are well-rested and ready for a session. "
                    f"Consider adding {example} exercises to your next workout."
                ),
                "rationale": "Balanced muscle group training reduces injury risk and improves overall strength.",
                "evidence_summary": (
                    f"Recently trained: {', '.join(sorted(recent_muscles)) or 'none'}. "
                    f"Rested groups: {', '.join(sorted(rested_muscles))}."
                ),
                "confidence": 0.65,
                "source_type": "rule",
            }
        )

    # ── 4. Preferred activities ─────────────────────────────────────────────
    for activity in preferred[:3]:
        if activity in impossible or activity in disliked:
            continue
        suggestions.append(
            {
                "category": "activity",
                "subject_type": "exercise",
                "subject_name": activity,
                "title": f"Go {activity}",
                "text": f"You enjoy {activity} — it's a great option for today's workout.",
                "rationale": "Suggests an activity the user has expressed preference for.",
                "evidence_summary": f"User preference signal: likes/preferred for '{activity}'.",
                "confidence": 0.8,
                "source_type": "preference",
            }
        )

    # ── 5. General catalog suggestions (filtered) ──────────────────────────
    catalog_added = 0
    for activity in _DEFAULT_ACTIVITIES:
        name = activity["name"]
        if name in impossible or name in disliked or name in preferred:
            continue
        intensity = activity["intensity"]
        # If recently worked out intensely, prefer low intensity
        if days_since is not None and days_since <= 1 and intensity == "high":
            continue
        suggestions.append(
            {
                "category": "activity",
                "subject_type": "exercise",
                "subject_name": name,
                "title": f"Try {name.title()} today",
                "text": (
                    f"A {intensity}-intensity {name} session is a good choice to maintain "
                    "your activity level."
                ),
                "rationale": "General wellness activity recommendation.",
                "evidence_summary": (
                    f"Activity type: {activity['type']}. Intensity: {intensity}. "
                    f"Days since last workout: {days_since}."
                ),
                "confidence": 0.55,
                "source_type": "rule",
            }
        )
        catalog_added += 1
        if catalog_added >= 2:
            break

    # De-duplicate and respect limit
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in suggestions:
        if s["title"] not in seen:
            seen.add(s["title"])
            unique.append(s)

    return unique[:limit]
