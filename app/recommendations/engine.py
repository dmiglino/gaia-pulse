"""Main recommendation engine for GaiaPulse.

Orchestrates generators, filters, and scoring to produce Suggestion records.

Usage::

    from app.recommendations.engine import RecommendationEngine

    engine = RecommendationEngine()
    suggestions = engine.generate_for_user(db, user, limit=10)
    household_suggestions = engine.generate_for_household(db, household, limit=5)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.models.user import User
from app.recommendations import filters, scorer
from app.recommendations.generators import (
    activity_generator,
    blood_generator,
    meal_generator,
    pantry_generator,
)

logger = logging.getLogger(__name__)

_RECENT_SUGGESTION_DAYS = 7
_RECENT_SIGNAL_DAYS = 30

#: `record_feedback` vivía acá: una segunda implementación completa del camino de
#: escritura de feedback, con `db.get(Suggestion, id)` sin chequeo de pertenencia y la
#: señal de aprendizaje grabada contra quien apretaba el botón. Cero llamadores — el
#: único camino real es `SuggestionService.respond_to_suggestion`, que ahora sí filtra.
#: Se borró en vez de arreglarse: dos implementaciones de la misma escritura es cómo se
#: filtró la primera vez, y esta además tocaba modelos desde fuera de `repositories/`.


class RecommendationEngine:
    """Generate and manage recommendations for users and households."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_for_user(
        self,
        db: Session,
        user: User,
        limit: int = 10,
    ) -> list[Suggestion]:
        """Generate personalised suggestions for a single user.

        Runs meal and activity generators, applies hard constraints,
        scores against behaviour signals, and persists new Suggestion rows.

        Args:
            db: Active SQLAlchemy session.
            user: The target User.
            limit: Maximum number of Suggestion records to create/return.

        Returns:
            List of freshly created (or existing pending) Suggestion records.
        """
        preferences = self._get_preferences(db, user.id)
        signals = self._get_signals(db, user.id)
        recent_suggestions = self._get_recent_suggestions_for_user(db, user.id)

        # ── Gather candidates ──────────────────────────────────────────
        candidates: list[dict[str, Any]] = []
        candidates += meal_generator.generate(db, user, preferences)
        candidates += activity_generator.generate(db, user, preferences)

        # Blood-analysis-driven suggestions (if analysis data exists)
        try:
            from app.services.blood_analysis_service import BloodAnalysisService
            blood_values = BloodAnalysisService(db).get_latest_values(user.id)
            candidates += blood_generator.generate(db, user, blood_values)
        except Exception:
            logger.exception("Blood generator failed for user_id=%d — skipping", user.id)

        # ── Hard constraint filter (explicit blocks) ───────────────────
        candidates = filters.apply_hard_constraints(candidates, user, preferences)

        # ── Signal constraint filter (rejected items from history) ─────
        candidates = filters.apply_signal_constraints(candidates, signals)

        # ── Score and rank ─────────────────────────────────────────────
        ranked = scorer.score_candidates(candidates, user, signals, recent_suggestions)

        # ── Persist top-N as Suggestion records ────────────────────────
        created: list[Suggestion] = []
        for item in ranked[:limit]:
            suggestion = self._make_user_suggestion(user, item)
            db.add(suggestion)
            created.append(suggestion)

        try:
            db.commit()
            for s in created:
                db.refresh(s)
        except Exception:
            db.rollback()
            logger.exception("Failed to commit user suggestions for user_id=%d", user.id)
            raise

        logger.info(
            "Generated %d suggestion(s) for user_id=%d.", len(created), user.id
        )
        return created

    def generate_for_household(
        self,
        db: Session,
        household: Household,
        limit: int = 5,
    ) -> list[Suggestion]:
        """Generate household-scoped suggestions (primarily pantry/shopping).

        Args:
            db: Active SQLAlchemy session.
            household: The target Household.
            limit: Maximum number of Suggestion records to create/return.

        Returns:
            List of freshly created Suggestion records scoped to the household.
        """
        candidates = pantry_generator.generate(db, household.id, limit=limit * 2)

        # For household suggestions we skip user-specific filtering
        created: list[Suggestion] = []
        for item in candidates[:limit]:
            suggestion = self._make_household_suggestion(household, item)
            db.add(suggestion)
            created.append(suggestion)

        try:
            db.commit()
            for s in created:
                db.refresh(s)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to commit household suggestions for household_id=%d", household.id
            )
            raise

        logger.info(
            "Generated %d household suggestion(s) for household_id=%d.",
            len(created),
            household.id,
        )
        return created

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_preferences(
        self, db: Session, user_id: int
    ) -> list[RecommendationPreference]:
        return (
            db.query(RecommendationPreference)
            .filter(RecommendationPreference.user_id == user_id)
            .all()
        )

    def _get_signals(self, db: Session, user_id: int) -> list[BehaviorSignal]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SIGNAL_DAYS)
        return (
            db.query(BehaviorSignal)
            .filter(
                BehaviorSignal.user_id == user_id,
                BehaviorSignal.created_at >= cutoff,
            )
            .order_by(BehaviorSignal.created_at.desc())
            .all()
        )

    def _get_recent_suggestions_for_user(
        self, db: Session, user_id: int
    ) -> list[Suggestion]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_RECENT_SUGGESTION_DAYS)
        return (
            db.query(Suggestion)
            .filter(
                Suggestion.scope_user_id == user_id,
                Suggestion.created_at >= cutoff,
            )
            .order_by(Suggestion.created_at.desc())
            .all()
        )

    def _make_user_suggestion(
        self, user: User, item: dict[str, Any]
    ) -> Suggestion:
        score = float(item.get("_score", item.get("confidence", 0.5)))
        return Suggestion(
            scope_type="user",
            household_id=user.household_id,
            scope_user_id=user.id,
            category=item.get("category", "meal"),
            title=item["title"],
            text=item["text"],
            rationale=item.get("rationale", ""),
            evidence_summary=item.get("evidence_summary"),
            confidence=round(min(max(score, 0.0), 1.0), 3),
            priority=self._confidence_to_priority(score),
            source_type=item.get("source_type", "rule"),
            status="pending",
        )

    def _make_household_suggestion(
        self, household: Household, item: dict[str, Any]
    ) -> Suggestion:
        score = float(item.get("_score", item.get("confidence", 0.5)))
        return Suggestion(
            scope_type="household",
            household_id=household.id,
            scope_user_id=None,
            category=item.get("category", "shopping"),
            title=item["title"],
            text=item["text"],
            rationale=item.get("rationale", ""),
            evidence_summary=item.get("evidence_summary"),
            confidence=round(min(max(score, 0.0), 1.0), 3),
            priority=self._confidence_to_priority(score),
            source_type=item.get("source_type", "rule"),
            status="pending",
        )

    @staticmethod
    def _confidence_to_priority(confidence: float) -> int:
        """Map confidence 0–1 to priority 1–10."""
        return max(1, min(10, round(confidence * 10)))
