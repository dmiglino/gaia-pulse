"""Background job that triggers recommendation engine for all active users."""
import logging

from app.db.session import SessionLocal
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


def run_suggestion_generation() -> None:
    """Generate fresh suggestions for all active users."""
    logger.info("Running suggestion generation job")
    db = SessionLocal()
    try:
        #: Vía repositorio y no con un `select(User)` acá: `app/jobs/` armando
        #: consultas sobre un modelo es lo que la regla de capas de `AGENTS.md`
        #: reserva a los repositorios.
        users = UserRepository(db).list_active()

        # Import here to avoid circular deps at module load time
        from app.recommendations.engine import RecommendationEngine
        engine = RecommendationEngine()

        #: No hay guardia de "¿existe el hogar?": `User.household_id` es `nullable=False`
        #: con FK, así que un usuario activo sin hogar no es un estado que la base permita.
        #: El `db.get(Household, ...)` que había acá no podía dar falso nunca.
        for user in users:
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
