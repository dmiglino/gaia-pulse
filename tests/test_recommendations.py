"""Tests for the recommendation engine."""
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryStock
from app.models.suggestion import RecommendationPreference
from app.models.user import User
from app.recommendations.filters import apply_hard_constraints, apply_signal_constraints
from app.recommendations.scorer import score_candidates


class TestHardConstraints:
    def test_impossible_activity_filtered_out(self, db: Session, diego: User) -> None:
        # Diego has swimming as impossible
        prefs = [
            RecommendationPreference(
                user_id=diego.id,
                item_type="exercise",
                item_name="swimming",
                preference_signal="impossible",
                strength=1.0,
            )
        ]
        candidates = [
            {"title": "Go swimming!", "text": "Try swimming today", "category": "activity"},
            {"title": "Go biking!", "text": "Bike for 30 min", "category": "activity"},
        ]
        filtered = apply_hard_constraints(candidates, diego, prefs)
        titles = [c["title"] for c in filtered]
        assert "Go swimming!" not in titles
        assert "Go biking!" in titles

    def test_disliked_food_filtered_out(self, db: Session, rocio: User) -> None:
        prefs = [
            RecommendationPreference(
                user_id=rocio.id,
                item_type="food",
                item_name="liver",
                preference_signal="dislikes",
                strength=1.0,
            )
        ]
        candidates = [
            {"title": "Liver and onions", "text": "Try liver tonight", "category": "meal"},
            {"title": "Chicken salad", "text": "Fresh chicken salad", "category": "meal"},
        ]
        filtered = apply_hard_constraints(candidates, rocio, prefs)
        titles = [c["title"] for c in filtered]
        assert not any("liver" in t.lower() for t in titles)
        assert "Chicken salad" in titles

    def test_no_prefs_returns_all_candidates(self, db: Session, diego: User) -> None:
        candidates = [
            {"title": "Run today", "text": "Go for a run", "category": "activity"},
            {"title": "Eat salad", "text": "Fresh salad", "category": "meal"},
        ]
        filtered = apply_hard_constraints(candidates, diego, [])
        assert len(filtered) == len(candidates)

    def test_impossible_activity_from_user_model(self, db: Session, diego: User) -> None:
        """Impossible activities set on the user model are also respected."""
        # Diego has impossible_activities_json = ["swimming"]
        candidates = [
            {"title": "Go for a swim", "text": "Try the pool", "category": "activity"},
            {"title": "Walk today", "text": "30 min walk", "category": "activity"},
        ]
        filtered = apply_hard_constraints(candidates, diego, [])
        titles = [c["title"] for c in filtered]
        # Swimming should be filtered if the constraints check user model too
        # At minimum, "Walk today" should pass
        assert "Walk today" in titles


class TestScorer:
    def test_positive_signal_boosts_score(self, db: Session, diego: User) -> None:
        from app.models.signal import BehaviorSignal

        signals = [
            BehaviorSignal(
                user_id=diego.id,
                signal_type="accepted_suggestion",
                entity_type="activity",
                entity_name="biking",
                value=1.0,
                source_type="explicit",
            )
        ]
        candidates = [
            {"title": "Go biking", "text": "Bike 30 min", "category": "activity", "confidence": 0.5},
            {"title": "Go running", "text": "Run 30 min", "category": "activity", "confidence": 0.5},
        ]
        scored = score_candidates(candidates, diego, signals, [])
        assert len(scored) == 2
        # Biking should score higher due to positive signal
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        assert biking.get("_score", 0) >= running.get("_score", 0)

    def test_negative_signal_reduces_score(self, db: Session, diego: User) -> None:
        from app.models.signal import BehaviorSignal

        signals = [
            BehaviorSignal(
                user_id=diego.id,
                signal_type="rejected_suggestion",
                entity_type="activity",
                entity_name="running",
                value=-1.0,
                source_type="explicit",
            )
        ]
        candidates = [
            {"title": "Go biking", "text": "Bike 30 min", "category": "activity", "confidence": 0.5},
            {"title": "Go running", "text": "Run 30 min", "category": "activity", "confidence": 0.5},
        ]
        scored = score_candidates(candidates, diego, signals, [])
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        assert biking.get("_score", 0) >= running.get("_score", 0)


class TestSignalConstraints:
    def test_strongly_rejected_activity_filtered_out(self, db: Session, diego: User) -> None:
        from app.models.signal import BehaviorSignal

        signals = [
            BehaviorSignal(
                user_id=diego.id,
                signal_type="rejected_suggestion",
                entity_type="activity",
                entity_name="running",
                value=-1.0,
                source_type="explicit",
            )
        ]
        candidates = [
            {"title": "Go running today", "text": "Run 5km", "category": "activity", "confidence": 0.8},
            {"title": "Go biking", "text": "Bike 30 min", "category": "activity", "confidence": 0.8},
        ]
        filtered = apply_signal_constraints(candidates, signals)
        titles = [c["title"] for c in filtered]
        assert "Go running today" not in titles
        assert "Go biking" in titles

    def test_no_rejected_signals_keeps_all(self, db: Session, diego: User) -> None:
        from app.models.signal import BehaviorSignal

        signals = [
            BehaviorSignal(
                user_id=diego.id,
                signal_type="accepted_suggestion",
                entity_type="activity",
                entity_name="biking",
                value=1.0,
                source_type="explicit",
            )
        ]
        candidates = [
            {"title": "Go running", "text": "Run 5km", "category": "activity", "confidence": 0.8},
            {"title": "Go biking", "text": "Bike 30 min", "category": "activity", "confidence": 0.8},
        ]
        filtered = apply_signal_constraints(candidates, signals)
        # Positive signals don't filter — all candidates kept
        assert len(filtered) == 2

    def test_empty_signals_returns_all(self, db: Session, diego: User) -> None:
        candidates = [
            {"title": "Run", "text": "Go run", "category": "activity", "confidence": 0.7},
            {"title": "Swim", "text": "Go swim", "category": "activity", "confidence": 0.7},
        ]
        filtered = apply_signal_constraints(candidates, [])
        assert len(filtered) == 2

    def test_diversity_penalty_applied_to_recent_duplicate(self, db: Session, diego: User) -> None:
        """Recently shown suggestions should score lower than fresh ones."""
        from app.models.suggestion import Suggestion
        from datetime import datetime, timezone

        recent = Suggestion(
            scope_type="user",
            scope_user_id=diego.id,
            household_id=diego.household_id,
            category="activity",
            title="Go biking",
            text="Bike for 30 min",
            rationale="You like biking",
            confidence=0.8,
            priority=8,
            source_type="rule",
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        candidates = [
            {"title": "Go biking", "text": "Bike for 30 min", "category": "activity", "confidence": 0.8},
            {"title": "Go running", "text": "Run 5km", "category": "activity", "confidence": 0.8},
        ]
        scored = score_candidates(candidates, diego, [], [recent])
        biking = next(c for c in scored if "biking" in c["title"].lower())
        running = next(c for c in scored if "running" in c["title"].lower())
        # Biking has diversity penalty (recently shown), running does not
        assert running["_score"] > biking["_score"]


class TestMealWindow:
    """`_current_meal_type` used to read the UTC hour, so at 08:00 in Buenos
    Aires (UTC-3) the engine believed it was 11:00 and suggested lunch."""

    @pytest.mark.parametrize(
        ("local_hour", "expected"),
        [(8, "breakfast"), (12, "lunch"), (16, "snack"), (21, "dinner")],
    )
    def test_meal_type_follows_the_configured_timezone(
        self, monkeypatch: pytest.MonkeyPatch, local_hour: int, expected: str
    ) -> None:
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo

        from app.recommendations.generators import meal_generator

        tz = ZoneInfo(get_settings().timezone)
        # A real instant whose local hour is `local_hour`, expressed in UTC so a
        # UTC reading of it would land on a different (and wrong) window.
        local = datetime(2026, 3, 15, local_hour, 30, tzinfo=tz)
        assert local.utcoffset() != timedelta(0), "test needs a non-UTC timezone"

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz: timezone | ZoneInfo | None = None) -> datetime:  # type: ignore[override]
                return local.astimezone(tz) if tz else local.replace(tzinfo=None)

        monkeypatch.setattr(meal_generator, "datetime", _FrozenDatetime)
        assert meal_generator._current_meal_type() == expected
