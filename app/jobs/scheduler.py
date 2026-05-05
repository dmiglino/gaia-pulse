import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=settings.timezone)
    return _scheduler


def start_scheduler() -> None:
    if not settings.enable_background_jobs:
        logger.info("Background jobs disabled")
        return

    scheduler = get_scheduler()

    from app.jobs.notification_jobs import (
        run_inactivity_notifications,
        run_low_stock_notifications,
        run_metric_reminder_notifications,
    )
    from app.jobs.suggestion_jobs import run_suggestion_generation

    scheduler.add_job(
        run_low_stock_notifications,
        trigger=IntervalTrigger(hours=6),
        id="low_stock_notifications",
        replace_existing=True,
    )
    scheduler.add_job(
        run_inactivity_notifications,
        trigger=IntervalTrigger(hours=24),
        id="inactivity_notifications",
        replace_existing=True,
    )
    scheduler.add_job(
        run_metric_reminder_notifications,
        trigger=IntervalTrigger(hours=24),
        id="metric_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        run_suggestion_generation,
        trigger=IntervalTrigger(
            minutes=settings.suggestion_job_interval_minutes
        ),
        id="suggestion_generation",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Background scheduler started with %d jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
