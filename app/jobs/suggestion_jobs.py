"""Background job that triggers recommendation engine for all active users."""
import logging

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def run_suggestion_generation() -> None:
    """Generate fresh suggestions for all active users."""
    logger.info("Running suggestion generation job")
    db = SessionLocal()
    try:
        from sqlalchemy import select
        from app.models.user import User
        from app.models.household import Household

        users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())

        # Import here to avoid circular deps at module load time
        from app.recommendations.engine import RecommendationEngine
        engine = RecommendationEngine()

        for user in users:
            household = db.get(Household, user.household_id)
            if not household:
                continue
            try:
                new_suggestions = engine.generate_for_user(db, user, limit=5)
                logger.info(
                    "Generated %d suggestions for user %d", len(new_suggestions), user.id
                )
            except Exception:
                logger.exception("Error generating suggestions for user %d", user.id)

    except Exception:
        logger.exception("Error in suggestion_generation job")
    finally:
        db.close()
