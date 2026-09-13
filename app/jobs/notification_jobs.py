"""Background jobs that generate system notifications."""

import logging

from app.core.clock import as_utc, is_quiet_hours, local_now
from app.db.session import SessionLocal
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.pantry_repo import PantryStockRepository
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


def _muted(job: str) -> bool:
    """¿Hay que callarse porque estamos en el horario de silencio?

    El gate va acá, en la puerta de los tres jobs, y no dentro de
    `NotificationService.create`: los únicos llamadores de `create` son estos
    tres, porque `app/web/` y `app/api/` solo leen, marcan, descartan y
    posponen. Meterle el horario al servicio sería un parámetro para un llamador
    que no existe.

    El job se saltea entero, no se posterga: `CronTrigger` lo va a volver a
    llamar mañana a la misma hora local, y lo que se está por avisar — stock
    bajo, inactividad, pesaje atrasado — sigue siendo cierto mañana. Un aviso
    guardado para las 8:01 no es más útil que el de las 8:20 del día siguiente,
    y no hay cola donde guardarlo.

    Con el `_SCHEDULE` que se envía — 08:20, 09:10 y 13:05 contra una franja de
    22 a 8 — ninguno de los tres jobs cae adentro, así que este gate no se
    dispara nunca y ese log no aparece. Es a propósito: la defensa que importa es
    que mover una hora en `_SCHEDULE`, o bajar `QUIET_HOURS_START`, no pueda
    volver a poner una notificación a las 3 de la mañana.
    """
    if not is_quiet_hours():
        return False
    logger.info("Job %s skipped: quiet hours (%s local)", job, local_now().strftime("%H:%M"))
    return True


def run_low_stock_notifications() -> None:
    """Generate notifications for pantry items below their threshold."""
    if _muted("low_stock"):
        return
    logger.info("Running low stock notification job")
    db = SessionLocal()
    try:
        pantry_repo = PantryStockRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        # Get all households (one pass per household)
        from sqlalchemy import select
        from app.models.household import Household

        households = list(db.scalars(select(Household)).all())

        for household in households:
            #: Desde que el job pasó a `CronTrigger` corre una vez por día, así que
            #: esta ventana de 6h — y las de 48h y 72h de los otros dos jobs — es más
            #: corta que el período de su propio job y **ya no suprime nada**: entre
            #: dos corridas siempre pasaron 24h. Queda porque no molesta y porque el
            #: chequeo que sí hace falta es por sujeto, no por categoría; lo reemplaza
            #: `has_recent_for_subject` en la 4.2 (ver docs/v3-plan.md §F2).
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

            notif_svc.create(
                NotificationCreate(
                    household_id=household.id,
                    category="low_stock",
                    title=f"Low pantry stock ({len(low_items)} item{'s' if len(low_items) > 1 else ''})",
                    body=body,
                    priority=7,
                    source_type="job",
                )
            )
            logger.info("Created low_stock notification for household %d", household.id)

    except Exception:
        logger.exception("Error in low_stock job")
    finally:
        db.close()


def run_inactivity_notifications() -> None:
    """Notify users who haven't logged a workout in N days."""
    INACTIVITY_THRESHOLD_DAYS = 4
    if _muted("inactivity"):
        return
    logger.info("Running inactivity notification job")
    db = SessionLocal()
    try:
        workout_repo = WorkoutRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        from sqlalchemy import select
        from app.models.user import User

        users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())

        for user in users:
            #: Inerte con el job diario, como el de `low_stock`.
            if notif_repo.has_recent_by_category(
                user.household_id, "inactivity", hours=48, user_id=user.id
            ):
                continue

            recent = workout_repo.get_user_recent_sessions(
                user.id, user.household_id, days=INACTIVITY_THRESHOLD_DAYS
            )
            if recent:
                continue

            notif_svc.create(
                NotificationCreate(
                    user_id=user.id,
                    household_id=user.household_id,
                    category="inactivity",
                    title=f"No workouts logged in {INACTIVITY_THRESHOLD_DAYS}+ days",
                    body=f"Hey {user.display_name}, it's been a while since your last workout. Consider a light session or walk today!",
                    priority=5,
                    source_type="job",
                )
            )
            logger.info("Created inactivity notification for user %d", user.id)

    except Exception:
        logger.exception("Error in inactivity job")
    finally:
        db.close()


def run_metric_reminder_notifications() -> None:
    """Remind users to log their weight if they haven't done so recently."""
    REMINDER_DAYS = 3
    if _muted("metric_reminder"):
        return
    logger.info("Running metric reminder job")
    db = SessionLocal()
    try:
        metric_repo = BodyMetricRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        from sqlalchemy import select
        from app.models.user import User

        users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())

        for user in users:
            #: Inerte con el job diario, como el de `low_stock`.
            if notif_repo.has_recent_by_category(
                user.household_id, "metric_reminder", hours=72, user_id=user.id
            ):
                continue

            latest = metric_repo.get_latest_for_user(user.id)
            if latest:
                #: `as_utc` **convierte**; el `.replace(tzinfo=utc)` que había acá
                #: **afirmaba**. Sobre una columna `timestamptz`, que en Postgres
                #: vuelve ya aware en la timezone de la sesión, eso corría el
                #: instante tantas horas como tenga el offset: con -03:00, un
                #: pesaje de hace 2 días y 22 horas se leía como de hace 3 y el
                #: recordatorio salía un día antes de lo que dice `REMINDER_DAYS`.
                age_days = (local_now() - as_utc(latest.timestamp)).days
                if age_days < REMINDER_DAYS:
                    continue

            notif_svc.create(
                NotificationCreate(
                    user_id=user.id,
                    household_id=user.household_id,
                    category="metric_reminder",
                    title="Time to log your weight",
                    body=f"{user.display_name}, you haven't logged your weight in a while. Tracking trends helps us give you better suggestions.",
                    priority=4,
                    source_type="job",
                )
            )

    except Exception:
        logger.exception("Error in metric_reminder job")
    finally:
        db.close()
