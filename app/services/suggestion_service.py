from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.suggestion import Suggestion
from app.models.user import User
from app.repositories.suggestion_repo import BehaviorSignalRepository, SuggestionRepository
from app.schemas.suggestion import RecommendationPreferenceCreate, SuggestionFeedback


class SuggestionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SuggestionRepository(db)
        self.signal_repo = BehaviorSignalRepository(db)

    def get_pending(self, user_id: int, household_id: int) -> list[Suggestion]:
        return self.repo.get_pending_for_user(user_id, household_id)

    def generate_for_user(self, user: User, limit: int = 10) -> list[Suggestion]:
        """Run the recommendation engine on demand for *user*."""
        from app.recommendations.engine import RecommendationEngine

        return RecommendationEngine().generate_for_user(self.db, user, limit=limit)

    def respond_to_suggestion(
        self,
        suggestion_id: int,
        feedback: SuggestionFeedback,
        user_id: int,
        household_id: int,
    ) -> Suggestion | None:
        """Guardar la respuesta a una sugerencia propia.

        `household_id` no está de adorno: sin él esto era `repo.get(suggestion_id)`, o
        sea que cualquier sesión válida podía responder por cualquier fila de la tabla.
        Ver `SuggestionRepository.get_owned`.
        """
        suggestion = self.repo.get_owned(suggestion_id, user_id, household_id)
        if not suggestion:
            return None

        suggestion.status = feedback.status
        suggestion.feedback_notes = feedback.feedback_notes
        suggestion.responded_at = datetime.now(timezone.utc)

        # Map feedback → behavior signal
        signal_map = {
            "accepted": ("accepted_suggestion", 1.0),
            "rejected": ("rejected_suggestion", -1.0),
            "dismissed": ("ignored_suggestion", -0.3),
            "snoozed": ("ignored_suggestion", 0.0),
        }
        if feedback.status in signal_map:
            signal_type, value = signal_map[feedback.status]
            # Determine the entity from the suggestion category
            entity_type = {
                "meal": "food",
                "activity": "exercise",
                "pantry": "food",
                "shopping": "food",
                "variety": "food",
            }.get(suggestion.category, "suggestion")

            self.signal_repo.record(
                user_id=user_id,
                signal_type=signal_type,
                entity_type=entity_type,
                entity_name=suggestion.title.lower()[:200],
                value=value,
                source_type="explicit",
                source_entity_type="suggestion",
                source_entity_id=suggestion.id,
            )

        self.db.flush()
        self.db.commit()
        return suggestion

    def save_preference(
        self, user_id: int, data: RecommendationPreferenceCreate
    ) -> None:
        self.repo.upsert_preference(
            user_id=user_id,
            item_type=data.item_type,
            item_name=data.item_name,
            signal=data.preference_signal,
            strength=data.strength,
            notes=data.notes,
        )
        # Also record as an explicit signal
        value = -1.0 if data.preference_signal in ("dislikes", "impossible", "avoid") else 1.0
        self.signal_repo.record(
            user_id=user_id,
            signal_type="explicit_preference",
            entity_type=data.item_type,
            entity_name=data.item_name,
            value=value,
            source_type="explicit",
        )
        self.db.commit()

    def get_user_preferences(self, user_id: int) -> list:
        return self.repo.get_user_preferences(user_id)
