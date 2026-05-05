"""Background jobs that generate system notifications."""
import logging
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.pantry_repo import PantryStockRepository
from app.repositories.user_repo import UserRepository
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


def run_low_stock_notifications() -> None:
    """Generate notifications for pantry items below their threshold."""
    logger.info("Running low stock notification job")
    db = SessionLocal()
    try:
        user_repo = UserRepository(db)
        pantry_repo = PantryStockRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        # Get all households (one pass per household)
        from sqlalchemy import select
        from app.models.household import Household
        households = list(db.scalars(select(Household)).all())

        for household in households:
            # Skip if we've already sent a low-stock notification in last 6 hours
            if notif_repo.has_recent_by_category(household.id, "low_stock", hours=6):
                continue

            low_items = pantry_repo.get_low_stock(household.id)
            if not low_items:
                continue

            names = ", ".join(i.food_item.canonical_name for i in low_items[:5])
            more = len(low_items) - 5
            body = f"Running low on: {names}"
            if more > 0:
                body += f" and {more} more items"

            notif_svc.create(NotificationCreate(
                household_id=household.id,
                category="low_stock",
                title=f"Low pantry stock ({len(low_items)} item{'s' if len(low_items) > 1 else ''})",
                body=body,
                priority=7,
                source_type="job",
            ))
            logger.info("Created low_stock notification for household %d", household.id)

    except Exception:
        logger.exception("Error in low_stock job")
    finally:
        db.close()


def run_inactivity_notifications() -> None:
    """Notify users who haven't logged a workout in N days."""
    INACTIVITY_THRESHOLD_DAYS = 4
    logger.info("Running inactivity notification job")
    db = SessionLocal()
    try:
        user_repo = UserRepository(db)
        workout_repo = WorkoutRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        from sqlalchemy import select
        from app.models.user import User
        users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())

        for user in users:
            # Skip if already sent recently
            if notif_repo.has_recent_by_category(
                user.household_id, "inactivity", hours=48, user_id=user.id
            ):
                continue

            recent = workout_repo.get_user_recent_sessions(
                user.id, user.household_id, days=INACTIVITY_THRESHOLD_DAYS
            )
            if recent:
                continue

            notif_svc.create(NotificationCreate(
                user_id=user.id,
                household_id=user.household_id,
                category="inactivity",
                title=f"No workouts logged in {INACTIVITY_THRESHOLD_DAYS}+ days",
                body=f"Hey {user.display_name}, it's been a while since your last workout. Consider a light session or walk today!",
                priority=5,
                source_type="job",
            ))
            logger.info("Created inactivity notification for user %d", user.id)

    except Exception:
        logger.exception("Error in inactivity job")
    finally:
        db.close()


def run_metric_reminder_notifications() -> None:
    """Remind users to log their weight if they haven't done so recently."""
    REMINDER_DAYS = 3
    logger.info("Running metric reminder job")
    db = SessionLocal()
    try:
        user_repo = UserRepository(db)
        metric_repo = BodyMetricRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        from sqlalchemy import select
        from app.models.user import User
        users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())

        for user in users:
            if notif_repo.has_recent_by_category(
                user.household_id, "metric_reminder", hours=72, user_id=user.id
            ):
                continue

            latest = metric_repo.get_latest_for_user(user.id)
            if latest:
                age_days = (datetime.now(timezone.utc) - latest.timestamp.replace(tzinfo=timezone.utc)).days
                if age_days < REMINDER_DAYS:
                    continue

            notif_svc.create(NotificationCreate(
                user_id=user.id,
                household_id=user.household_id,
                category="metric_reminder",
                title="Time to log your weight",
                body=f"{user.display_name}, you haven't logged your weight in a while. Tracking trends helps us give you better suggestions.",
                priority=4,
                source_type="job",
            ))

    except Exception:
        logger.exception("Error in metric_reminder job")
    finally:
        db.close()
