"""Los jobs que escriben notificaciones del sistema.

Los tres avisan de una **ausencia o un faltante**, y hasta la 4.2 los tres avisaban
de lo mismo una y otra vez: el único control era un cooldown por categoría, así que
la leche baja desde hace tres semanas producía un aviso nuevo por corrida — con el
cuerpo recalculado y sin ninguna memoria de que ya se había avisado.

Ahora cada aviso declara su **sujeto** en `related_entity_type`/`related_entity_id`
(el ítem de despensa, la persona) y se emite una vez por sujeto. Se vuelve a hablar
solo si la cosa **empeoró** — la despensa más vacía, la inactividad más larga —, y
eso se mide con `priority`: cada job traduce el estado a una severidad y
`has_recent_for_subject` la compara contra la del último aviso del mismo sujeto.
Pasada la ventana de `_SUBJECT_WINDOW_DAYS` el sujeto vuelve a estar disponible, para
que un faltante que sigue ahí no quede en silencio para siempre.
"""

import logging

from app.core.clock import as_utc, is_quiet_hours, local_now
from app.db.session import SessionLocal
from app.models.pantry import PantryStock
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.household_repo import HouseholdRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.pantry_repo import PantryStockRepository
from app.repositories.user_repo import UserRepository
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

#: Cuánto calla un sujeto cuya severidad no cambió. Es el techo del silencio, no el
#: mecanismo: lo normal es que el sujeto quede callado porque ya se avisó, y esta
#: ventana existe para que un faltante que sigue ahí a la semana siguiente vuelva a
#: aparecer una vez en vez de desaparecer para siempre.
_SUBJECT_WINDOW_DAYS = 7

#: Tope de avisos **nuevos** de despensa por hogar y por corrida. Un aviso por ítem
#: es lo que permite escalar y lo que le da a la 4.3 a dónde ir al tocarlo, pero una
#: despensa recién cargada con quince faltantes no puede convertirse en quince
#: notificaciones el mismo día. Con el tope se drenan a razón de cinco por día y cada
#: uno se calla solo, en el orden de `_most_urgent_first`. El drenaje alcanza mientras
#: los faltantes sean menos que `_MAX_NEW_STOCK_ALERTS × _SUBJECT_WINDOW_DAYS`: pasado
#: eso los sujetos de hace una semana vuelven a ser elegibles y se comen el tope antes
#: de que llegue la cola. Son 35 ítems en falta a la vez, que no es una despensa de dos.
_MAX_NEW_STOCK_ALERTS = 5

#: Cuánto se guarda una notificación antes de la poda.
_PRUNE_AFTER_DAYS = 90

#: Días sin entrenar a partir de los cuales se avisa — y el escalón de la escalada:
#: se vuelve a avisar a los 8, a los 12, no todos los días.
_INACTIVITY_THRESHOLD_DAYS = 4

#: Ídem para el pesaje.
_REMINDER_DAYS = 3


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


def _fmt_qty(value: float | None) -> str:
    """`2` y no `2.000`: la columna es `Numeric(10, 3)` y el cuerpo lo lee una persona."""
    return f"{float(value or 0):.3f}".rstrip("0").rstrip(".")


def _stock_severity(stock: PantryStock) -> int:
    """Cuán urgente es este faltante, en la escala de `priority`.

    Tres escalones y no un número continuo: son los tres estados que a alguien le
    cambian el plan del día — está bajando, está por acabarse, se acabó — y la
    severidad es lo que decide si se vuelve a hablar de este ítem. Con un valor
    continuo, medio litro menos de leche sería un aviso nuevo.
    """
    quantity = float(stock.current_quantity)
    if quantity <= 0:
        return 9
    #: Sin caso para "no hay umbral": todos los que llegan acá pasaron por
    #: `PantryStock.is_low`, y con `quantity > 0` esa propiedad solo da verdadero si el
    #: umbral existe y es positivo. Un caso para el umbral ausente sería una rama
    #: inalcanzable que además hace parecer que la ausencia significa algo.
    threshold = float(stock.low_stock_threshold or 0)
    if quantity <= threshold / 2:
        return 8
    return 7


def _most_urgent_first(items: list[PantryStock]) -> list[PantryStock]:
    """Ordena los faltantes para que el tope de la corrida sea determinista.

    Sin un orden explícito el tope se lo reparte el orden en que el motor devolvió
    las filas (regla 5), que puede cambiar entre corridas: quién se enteró de que no
    hay café dependería del plan de la consulta.
    """
    return sorted(items, key=lambda s: (-_stock_severity(s), s.food_item_id))


def _steps_since(days_since: int, threshold: int) -> int:
    """Cuántos umbrales enteros lleva pasados, para escalar de a un punto."""
    return max(0, (days_since - threshold) // threshold)


def run_low_stock_notifications() -> None:
    """Avisar, una vez por ítem, de lo que se está acabando en la despensa."""
    if _muted("low_stock"):
        return
    logger.info("Running low stock notification job")
    db = SessionLocal()
    try:
        pantry_repo = PantryStockRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        for household in HouseholdRepository(db).list_all():
            low_items = pantry_repo.get_low_stock(household.id)
            if not low_items:
                continue

            created = 0
            for stock in _most_urgent_first(low_items):
                if created >= _MAX_NEW_STOCK_ALERTS:
                    break

                severity = _stock_severity(stock)
                #: El aviso es del hogar (`user_id` nulo): la despensa es de los dos.
                if notif_repo.has_recent_for_subject(
                    "low_stock",
                    "pantry_stock",
                    stock.id,
                    household_id=household.id,
                    days=_SUBJECT_WINDOW_DAYS,
                    severity=severity,
                ):
                    continue

                name = stock.food_item.canonical_name
                left = _fmt_qty(stock.current_quantity)
                if float(stock.current_quantity) <= 0:
                    title = f"Out of {name}"
                    body = f"There's no {name} left in the pantry."
                else:
                    title = f"Running low on {name}"
                    body = (
                        f"{left} {stock.unit} of {name} left, "
                        f"at or below the {_fmt_qty(stock.low_stock_threshold)} "
                        f"{stock.unit} you set as low."
                    )

                notif_svc.create(
                    NotificationCreate(
                        household_id=household.id,
                        category="low_stock",
                        title=title,
                        body=body,
                        priority=severity,
                        source_type="job",
                        related_entity_type="pantry_stock",
                        related_entity_id=stock.id,
                    )
                )
                created += 1

            if created:
                logger.info(
                    "Created %d low_stock notification(s) for household %d (%d low items)",
                    created,
                    household.id,
                    len(low_items),
                )

    except Exception:
        logger.exception("Error in low_stock job")
    finally:
        db.close()


def run_inactivity_notifications() -> None:
    """Notify users who haven't logged a workout in N days."""
    if _muted("inactivity"):
        return
    logger.info("Running inactivity notification job")
    db = SessionLocal()
    try:
        workout_repo = WorkoutRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        for user in UserRepository(db).list_active():
            last = workout_repo.get_last_session_start(user.id, user.household_id)
            if last is None:
                #: Nunca registró un entrenamiento: no hay antigüedad que medir, así
                #: que se queda en el escalón base y no escala. `days_since` queda en
                #: `None` en vez de en el umbral: un centinela acá terminaba adentro del
                #: título — "No workouts logged in 4 days" a alguien que nunca registró
                #: uno, y a una cuenta creada ayer — y eso es un número inventado.
                days_since, severity = None, 5
            else:
                days_since = (local_now() - as_utc(last)).days
                if days_since < _INACTIVITY_THRESHOLD_DAYS:
                    continue
                #: Un punto más por cada tanda de cuatro días: se avisó a los 4 con
                #: severidad 5, a los 8 la severidad es 6 y el aviso vuelve a salir
                #: porque empeoró. Entre el 4 y el 7 no se repite.
                severity = min(10, 5 + _steps_since(days_since, _INACTIVITY_THRESHOLD_DAYS))

            if notif_repo.has_recent_for_subject(
                "inactivity",
                "user",
                user.id,
                household_id=user.household_id,
                user_id=user.id,
                days=_SUBJECT_WINDOW_DAYS,
                severity=severity,
            ):
                continue

            if days_since is None:
                title = "No workouts logged yet"
                body = (
                    f"Hey {user.display_name}, there's no workout logged yet. A light "
                    "session or a walk is a good place to start!"
                )
            else:
                title = f"No workouts logged in {days_since} days"
                body = (
                    f"Hey {user.display_name}, it's been {days_since} days since your "
                    "last workout. Consider a light session or walk today!"
                )

            notif_svc.create(
                NotificationCreate(
                    user_id=user.id,
                    household_id=user.household_id,
                    category="inactivity",
                    title=title,
                    body=body,
                    priority=severity,
                    source_type="job",
                    related_entity_type="user",
                    related_entity_id=user.id,
                )
            )
            logger.info("Created inactivity notification for user %d", user.id)

    except Exception:
        logger.exception("Error in inactivity job")
    finally:
        db.close()


def run_metric_reminder_notifications() -> None:
    """Remind users to log their weight if they haven't done so recently."""
    if _muted("metric_reminder"):
        return
    logger.info("Running metric reminder job")
    db = SessionLocal()
    try:
        metric_repo = BodyMetricRepository(db)
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        for user in UserRepository(db).list_active():
            latest = metric_repo.get_latest_for_user(user.id)
            if latest is None:
                #: Nunca se pesó: ídem la inactividad, escalón base y sin número que
                #: informar — el centinela le decía "you haven't logged your weight in
                #: 3 days" a alguien que nunca lo hizo.
                days_since, severity = None, 4
            else:
                #: `as_utc` **convierte**; el `.replace(tzinfo=utc)` que había acá
                #: **afirmaba**. Sobre una columna `timestamptz`, que en Postgres
                #: vuelve ya aware en la timezone de la sesión, eso corría el
                #: instante tantas horas como tenga el offset: con -03:00, un
                #: pesaje de hace 2 días y 22 horas se leía como de hace 3 y el
                #: recordatorio salía un día antes de lo que dice `_REMINDER_DAYS`.
                days_since = (local_now() - as_utc(latest.timestamp)).days
                if days_since < _REMINDER_DAYS:
                    continue
                severity = min(10, 4 + _steps_since(days_since, _REMINDER_DAYS))

            if notif_repo.has_recent_for_subject(
                "metric_reminder",
                "user",
                user.id,
                household_id=user.household_id,
                user_id=user.id,
                days=_SUBJECT_WINDOW_DAYS,
                severity=severity,
            ):
                continue

            if days_since is None:
                body = (
                    f"{user.display_name}, there's no weight logged yet. Tracking trends "
                    "helps us give you better suggestions."
                )
            else:
                body = (
                    f"{user.display_name}, you haven't logged your weight in "
                    f"{days_since} days. Tracking trends helps us give you better "
                    "suggestions."
                )

            notif_svc.create(
                NotificationCreate(
                    user_id=user.id,
                    household_id=user.household_id,
                    category="metric_reminder",
                    title="Time to log your weight",
                    body=body,
                    priority=severity,
                    source_type="job",
                    related_entity_type="user",
                    related_entity_id=user.id,
                )
            )

    except Exception:
        logger.exception("Error in metric_reminder job")
    finally:
        db.close()


def run_notification_pruning() -> None:
    """Tirar las notificaciones viejas.

    No pasa por `_muted`: no le habla a nadie, y corre justamente en el medio de la
    franja de silencio porque es el rato en que nadie está mirando la pantalla que
    esta tabla alimenta.
    """
    logger.info("Running notification pruning job")
    db = SessionLocal()
    try:
        removed = NotificationRepository(db).prune_older_than(_PRUNE_AFTER_DAYS)
        db.commit()
        logger.info("Pruned %d notification(s) older than %d days", removed, _PRUNE_AFTER_DAYS)
    except Exception:
        logger.exception("Error in notification pruning job")
    finally:
        db.close()
