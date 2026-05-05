"""Activity / workout suggestion generator.

Generates activity suggestions for a user based on:
- Days since last workout (rest vs. activity nudge)
- Recent workout history (avoid over-training same muscle groups)
- User's preferred, disliked, and impossible activities
- Explicit RecommendationPreference entries
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession

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


def _days_since_last_workout(db: Session, user: User) -> int | None:
    """Return days since last workout or None if no history."""
    last = (
        db.query(WorkoutSession.timestamp_start)
        .join(WorkoutParticipant, WorkoutParticipant.workout_session_id == WorkoutSession.id)
        .filter(WorkoutParticipant.user_id == user.id)
        .order_by(WorkoutSession.timestamp_start.desc())
        .first()
    )
    if last is None:
        return None
    delta = datetime.now(tz=timezone.utc) - last[0].replace(tzinfo=timezone.utc)
    return delta.days


def _recently_trained_muscles(db: Session, user: User, days: int = _OVERTRAINING_DAYS) -> set[str]:
    """Return muscle groups trained in the last N days."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    rows = (
        db.query(WorkoutExercise.muscle_group)
        .join(WorkoutParticipant, WorkoutParticipant.id == WorkoutExercise.workout_participant_id)
        .join(WorkoutSession, WorkoutSession.id == WorkoutParticipant.workout_session_id)
        .filter(
            WorkoutParticipant.user_id == user.id,
            WorkoutSession.timestamp_start >= cutoff,
            WorkoutExercise.muscle_group.isnot(None),
        )
        .all()
    )
    return {r[0].lower() for r in rows if r[0]}


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
    db: Session,
    user: User,
    preferences: list[RecommendationPreference],
    limit: int = _MAX_SUGGESTIONS,
) -> list[dict[str, Any]]:
    """Generate activity suggestions for *user*.

    Returns a list of suggestion dicts.
    """
    suggestions: list[dict[str, Any]] = []

    days_since = _days_since_last_workout(db, user)
    recent_muscles = _recently_trained_muscles(db, user)
    impossible = _get_impossible_activities(user, preferences)
    disliked = _get_disliked_activities(user, preferences)
    preferred = _get_preferred_activities(user, preferences)

    # ── 1. Rest nudge: if trained yesterday / same day ─────────────────────
    if days_since is not None and days_since == 0:
        suggestions.append(
            {
                "category": "activity",
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
