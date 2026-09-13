"""El calendario de los jobs de fondo, en hora local y no en hora de arranque.

Los cuatro jobs eran `IntervalTrigger` — y `IntervalTrigger` sin `start_date`
dispara por primera vez en `now + interval`, así que la hora de cada corrida
quedaba anclada al momento en que arrancó el proceso: un deploy a las 03:00 dejaba
el aviso de despensa saliendo a las 03:00 y a las 09:00 todos los días, hasta el
próximo reinicio. `settings.timezone` estaba en la config, en el README y en
`.env.example`, y el único lugar que lo leía era `BackgroundScheduler(timezone=…)`,
donde sin un `CronTrigger` no tenía ningún efecto observable.

Ahora cada job tiene una hora local y una razón para tenerla, y esa hora no cambia
si el proceso se reinicia. Las horas están corridas de la punta de la hora a
propósito: cada job abre su propia sesión de base y no hay motivo para que compitan
en el mismo minuto.

`NOTIFICATION_JOB_INTERVAL_MINUTES` y `SUGGESTION_JOB_INTERVAL_MINUTES` se fueron
con esto. El primero no lo leía ningún código — estaba documentado y muerto —, y el
segundo describía exactamente lo que se está sacando: un intervalo cuyo efecto
dependía de la hora de arranque.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.clock import household_tz
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None

#: `id → (hora, minuto)` en hora local. El por qué de cada hora va en el comentario
#: de arriba de su entrada y no dentro de la tupla: es prosa para quien lee el
#: calendario, no un dato que el código use.
_SCHEDULE: dict[str, tuple[str, int]] = {
    # A la mañana, cuando todavía se puede comprar: antes de salir a hacer las
    # compras del día, no después.
    "low_stock_notifications": ("9", 10),
    # Al mediodía, con el día por delante: queda tiempo de hacer algo hoy; a la
    # noche el aviso ya es un reproche.
    "inactivity_notifications": ("13", 5),
    # Al arrancar el día, antes de desayunar: el pesaje sirve en ayunas.
    "metric_reminders": ("8", 20),
    # Después de cenar, cuando el día de comidas ya está completo: a la mañana el
    # hueco todavía no existe, y a media tarde el aviso sale mientras la persona
    # está por almorzar.
    "meal_reminders": ("20", 45),
    # A media mañana y no al despertarse: el sueño se anota cuando uno ya se
    # levantó, y a las 8:20 el aviso competiría con el del pesaje.
    "sleep_reminders": ("10", 25),
    # Dos veces: una para que Home tenga algo fresco a la mañana y otra antes de
    # que se decida la cena.
    "suggestion_generation": ("7,18", 40),
    # De madrugada y a propósito adentro de la franja de silencio: es limpieza, no
    # le habla a nadie, y es el rato en que nadie está mirando la pantalla que
    # la tabla que poda alimenta.
    "notification_pruning": ("4", 15),
}


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        #: Vía `household_tz()` y no `settings.timezone` a secas: con el string
        #: crudo, un `TIMEZONE` mal escrito no dejaba arrancar la app, mientras el
        #: resto del código promete degradar a UTC con un warning.
        _scheduler = BackgroundScheduler(timezone=household_tz())
    return _scheduler


def _trigger(job_id: str) -> CronTrigger:
    """El `CronTrigger` de *job_id*, en la timezone del hogar.

    La timezone va explícita en el trigger además de en el scheduler: es la que
    decide qué significan `hour` y `minute`, y dejarla implícita en el scheduler
    hacía que el calendario no se pudiera leer — ni testear — sin saber cómo se
    construyó el scheduler.
    """
    hour, minute = _SCHEDULE[job_id]
    return CronTrigger(hour=hour, minute=minute, timezone=household_tz())


def start_scheduler() -> None:
    if not settings.enable_background_jobs:
        logger.info("Background jobs disabled")
        return

    scheduler = get_scheduler()

    from app.jobs.notification_jobs import (
        run_inactivity_notifications,
        run_low_stock_notifications,
        run_meal_reminder_notifications,
        run_metric_reminder_notifications,
        run_notification_pruning,
        run_sleep_reminder_notifications,
    )
    from app.jobs.suggestion_jobs import run_suggestion_generation

    jobs = {
        "low_stock_notifications": run_low_stock_notifications,
        "inactivity_notifications": run_inactivity_notifications,
        "metric_reminders": run_metric_reminder_notifications,
        "meal_reminders": run_meal_reminder_notifications,
        "sleep_reminders": run_sleep_reminder_notifications,
        "suggestion_generation": run_suggestion_generation,
        "notification_pruning": run_notification_pruning,
    }
    for job_id, func in jobs.items():
        scheduler.add_job(func, trigger=_trigger(job_id), id=job_id, replace_existing=True)

    scheduler.start()
    logger.info(
        "Background scheduler started with %d jobs (timezone %s, quiet hours %02d–%02d)",
        len(scheduler.get_jobs()),
        household_tz(),
        settings.quiet_hours_start,
        settings.quiet_hours_end,
    )


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
