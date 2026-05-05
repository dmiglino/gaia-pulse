from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.signal import BehaviorSignal
from app.models.suggestion import RecommendationPreference, Suggestion
from app.repositories.base import BaseRepository


class SuggestionRepository(BaseRepository[Suggestion]):
    def __init__(self, db: Session) -> None:
        super().__init__(Suggestion, db)

    def get_pending_for_user(self, user_id: int, household_id: int) -> list[Suggestion]:
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.status == "pending",
                or_(
                    Suggestion.scope_user_id == user_id,
                    Suggestion.household_id == household_id,
                ),
            )
            .order_by(Suggestion.priority.desc(), Suggestion.created_at.desc())
            .limit(20)
        )
        return list(self.db.scalars(stmt).all())

    def get_recent_suggestions(
        self, user_id: int, household_id: int, days: int = 7
    ) -> list[Suggestion]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(Suggestion)
            .where(
                Suggestion.created_at >= cutoff,
                or_(
                    Suggestion.scope_user_id == user_id,
                    Suggestion.household_id == household_id,
                ),
            )
            .order_by(Suggestion.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_user_preferences(self, user_id: int) -> list[RecommendationPreference]:
        stmt = select(RecommendationPreference).where(
            RecommendationPreference.user_id == user_id
        )
        return list(self.db.scalars(stmt).all())

    def upsert_preference(
        self,
        user_id: int,
        item_type: str,
        item_name: str,
        signal: str,
        strength: float = 1.0,
        notes: str | None = None,
    ) -> RecommendationPreference:
        stmt = select(RecommendationPreference).where(
            RecommendationPreference.user_id == user_id,
            RecommendationPreference.item_type == item_type,
            RecommendationPreference.item_name == item_name.lower(),
        )
        pref = self.db.scalar(stmt)
        if pref is None:
            pref = RecommendationPreference(
                user_id=user_id,
                item_type=item_type,
                item_name=item_name.lower(),
                preference_signal=signal,
                strength=strength,
                notes=notes,
            )
            self.db.add(pref)
        else:
            pref.preference_signal = signal
            pref.strength = strength
            if notes:
                pref.notes = notes
            pref.updated_at = datetime.utcnow()
        self.db.flush()
        return pref


class BehaviorSignalRepository(BaseRepository[BehaviorSignal]):
    def __init__(self, db: Session) -> None:
        super().__init__(BehaviorSignal, db)

    def get_user_signals(
        self,
        user_id: int,
        signal_type: str | None = None,
        entity_type: str | None = None,
        limit: int = 200,
    ) -> list[BehaviorSignal]:
        stmt = (
            select(BehaviorSignal)
            .where(BehaviorSignal.user_id == user_id)
            .order_by(BehaviorSignal.created_at.desc())
            .limit(limit)
        )
        if signal_type:
            stmt = stmt.where(BehaviorSignal.signal_type == signal_type)
        if entity_type:
            stmt = stmt.where(BehaviorSignal.entity_type == entity_type)
        return list(self.db.scalars(stmt).all())

    def record(
        self,
        user_id: int,
        signal_type: str,
        entity_type: str,
        entity_name: str,
        value: float = 1.0,
        source_type: str = "implicit",
        entity_id: int | None = None,
        source_entity_type: str | None = None,
        source_entity_id: int | None = None,
        context: dict | None = None,
    ) -> BehaviorSignal:
        signal = BehaviorSignal(
            user_id=user_id,
            signal_type=signal_type,
            entity_type=entity_type,
            entity_name=entity_name.lower(),
            entity_id=entity_id,
            value=value,
            source_type=source_type,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            context_json=context,
        )
        self.db.add(signal)
        self.db.flush()
        return signal
