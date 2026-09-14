"""Los jobs que escriben notificaciones del sistema.

Todos avisan de una **ausencia o un faltante**, y hasta la 4.2 avisaban de lo mismo una
y otra vez: el único control era un cooldown por categoría, así que la leche baja desde
hace tres semanas producía un aviso nuevo por corrida — con el cuerpo recalculado y sin
ninguna memoria de que ya se había avisado.

Ahora cada aviso declara su **sujeto** en `related_entity_type`/`related_entity_id`
(el ítem de despensa, la persona) y se emite una vez por sujeto. Se vuelve a hablar
solo si la cosa **empeoró** — la despensa más vacía, la inactividad más larga —, y
eso se mide con `priority`: cada job traduce el estado a una severidad y
`has_recent_for_subject` la compara contra la del último aviso del mismo sujeto.
Pasada la ventana de `_SUBJECT_WINDOW_DAYS` el sujeto vuelve a estar disponible, para
que un faltante que sigue ahí no quede en silencio para siempre.

La otra mitad de ese ciclo es la que agrega la 4.3: **retirar** el aviso cuando el sujeto
sale del conjunto — la leche que se repuso, el que volvió a entrenar. Sin eso "no anotaste
tu peso en 4 días" seguía en la lista después del pesaje, y la marca de agua de `priority`
esperaba hasta el final de la ventana de siete días: la próxima vez que la leche se
acabara, la app se quedaba muda. El retiro es un `DELETE` y no una marca porque
`has_recent_for_subject` no mira `dismissed_at` a propósito; ver
`NotificationRepository._retire`.

Las cuatro ausencias por persona —entrenamiento, pesaje, comidas, sueño— comparten toda
esa lógica y se declaran como datos (`_Absence`) sobre un solo driver. Cuatro copias de la
misma cosa es exactamente cómo la v1 terminó con doce paletas paralelas: acá lo que no
puede divergir es el sujeto, la escalada y el retiro.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.clock import as_utc, is_quiet_hours, local_now
from app.db.session import SessionLocal
from app.jobs.logging_utils import log_job_error
from app.models.pantry import PantryStock
from app.models.user import User
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.household_repo import HouseholdRepository
from app.repositories.meal_repo import MealRepository
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


def _muted(job: str) -> bool:
    """¿Hay que callarse porque estamos en el horario de silencio?

    El gate va acá, en la puerta de cada job, y no dentro de
    `NotificationService.create`: los únicos llamadores de `create` son estos
    jobs, porque `app/web/` y `app/api/` solo leen, marcan, descartan y
    posponen. Meterle el horario al servicio sería un parámetro para un llamador
    que no existe.

    El job se saltea entero, no se posterga: `CronTrigger` lo va a volver a
    llamar mañana a la misma hora local, y lo que se está por avisar — stock
    bajo, inactividad, pesaje atrasado — sigue siendo cierto mañana. Un aviso
    guardado para las 8:01 no es más útil que el de las 8:20 del día siguiente,
    y no hay cola donde guardarlo.

    Con el `_SCHEDULE` que se envía — de 08:20 a 20:45, contra una franja de 22 a
    8 — ningún job que hable cae adentro, así que este gate no se dispara nunca y
    ese log no aparece. Es a propósito: la defensa que importa es que mover una
    hora en `_SCHEDULE`, o bajar `QUIET_HOURS_START`, no pueda volver a poner una
    notificación a las 3 de la mañana. El recordatorio de comidas es el que pasa
    más cerca del borde, y por eso es el que la deja más a la vista.

    Desde la 4.3 estos jobs además **retiran**, y saltear el job entero saltea también
    el retiro, que no le habla a nadie —es la razón por la que la poda no pasa por acá—.
    Se acepta a propósito: el retiro es idempotente y la corrida de mañana lo hace igual,
    así que el costo es una tarjeta vieja un día de más. Lo que sí queda anotado es el
    caso raro: si alguien pusiera `QUIET_HOURS_START=20`, el job de comidas de las 20:45
    quedaría mudo **siempre**, y su tarjeta abierta no se iría hasta la poda de los 90
    días. Mover una hora de `_SCHEDULE` adentro de la franja es apagar ese aviso, no
    postergarlo.
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

            #: Primero el retiro, y **antes** del `if not low_items`: la lista de faltantes
            #: de hoy es la verdad entera, así que todo aviso que nombre un ítem que no
            #: está en ella es de algo que se repuso. Con el `continue` adelante, una
            #: despensa recuperada por completo era justamente el caso que nunca se
            #: limpiaba. Un solo `DELETE` por hogar en vez de preguntar ítem por ítem.
            retired = notif_repo.retire_subjects_other_than(
                "low_stock",
                "pantry_stock",
                [stock.id for stock in low_items],
                household_id=household.id,
            )
            if retired:
                #: `_retire` hace `flush()`, no `commit()`, y el único que commitea acá es
                #: `NotificationService.create`: sin esto una corrida que solo retira se
                #: pierde entera en el `close()` del `finally`.
                db.commit()
                logger.info(
                    "Retired %d low_stock notification(s) for household %d", retired, household.id
                )

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

    except Exception as exc:
        #: Vía `log_job_error` y no `logger.exception` directo: lo que puede fallar acá
        #: es un `IntegrityError` del `INSERT` en `notif_svc.create`, y su `__str__()`
        #: lleva el nombre del ítem de despensa como parámetro atado — exactamente lo
        #: que este job existe para anunciar, no para escribir en un log.
        log_job_error(logger, "Error in low_stock job", exc)
    finally:
        db.close()


@dataclass(frozen=True)
class _Absence:
    """Una cosa que se espera que la persona registre, y que hace rato no registra.

    Las cuatro se comportan igual en todo lo que importa: leen cuándo fue la última vez,
    miden el hueco en días locales, escalan de a un punto por umbral pasado, se callan si
    ya avisaron de este sujeto con esta severidad, y se retiran cuando la persona vuelve a
    registrar. Eso es `_run_absence_job`, una sola vez. Lo que cambia entre ellas —qué se
    lee, cada cuántos días, con qué severidad de arranque y qué dice el texto— es esto.

    *base_severity* es el escalón de arranque y hace una escala de frecuencia entre las
    cuatro: se espera comer todos los días y pesarse cada tanto, así que el hueco de
    comidas se mira a los 2 días y el de entrenamiento a los 4. La severidad no se le
    muestra a nadie —`priority` no se renderiza en ninguna plantilla— y sirve solo como
    marca de agua de la escalada.

    *first_message* es el texto para quien **nunca** registró nada, y que sea opcional es
    la diferencia deliberada entre las dos ausencias viejas y las dos nuevas: al que nunca
    entrenó se le dice algo (es el comportamiento que ya estaba), pero al que nunca anotó
    una comida ni una hora de sueño, no. Una notificación señala un agujero en una
    costumbre; proponer una costumbre que nadie tiene es tarea del motor de sugerencias, y
    el día uno de la app no tiene que ser una pared de retos. Con `None`, quien no tiene
    registro simplemente no recibe nada — y `message` recibe un `int` de verdad en vez de
    un `int | None` con una rama inalcanzable adentro.
    """

    category: str
    threshold_days: int
    base_severity: int
    last_logged: Callable[[Session, User], datetime | None]
    message: Callable[[User, int], tuple[str, str]]
    first_message: Callable[[User], tuple[str, str]] | None = None


def _last_workout(db: Session, user: User) -> datetime | None:
    return WorkoutRepository(db).get_last_session_start(user.id, user.household_id)


def _last_weight(db: Session, user: User) -> datetime | None:
    #: Tampoco es la última medición corporal, por el mismo motivo que el sueño y en el
    #: sentido contrario: "dormí 7 horas" escribe una fila sin `weight_kg`, y contarla
    #: como pesaje retira el recordatorio del peso sin que nadie se haya pesado.
    return BodyMetricRepository(db).get_last_weight_at(user.id)


def _last_meal(db: Session, user: User) -> datetime | None:
    return MealRepository(db).get_last_meal_at(user.id, user.household_id)


def _last_sleep(db: Session, user: User) -> datetime | None:
    #: No es la última medición corporal: `sleep_hours` es una columna opcional de la
    #: misma fila que el peso, así que preguntar por la última fila diría "anotó sueño hoy"
    #: para alguien que se pesa todos los días y no anota sueño desde marzo.
    return BodyMetricRepository(db).get_last_sleep_at(user.id)


def _workout_message(user: User, days_since: int) -> tuple[str, str]:
    return (
        f"No workouts logged in {days_since} days",
        f"Hey {user.display_name}, it's been {days_since} days since your "
        "last workout. Consider a light session or walk today!",
    )


def _first_workout_message(user: User) -> tuple[str, str]:
    return (
        "No workouts logged yet",
        f"Hey {user.display_name}, there's no workout logged yet. A light "
        "session or a walk is a good place to start!",
    )


def _weight_message(user: User, days_since: int) -> tuple[str, str]:
    return (
        "Time to log your weight",
        f"{user.display_name}, you haven't logged your weight in "
        f"{days_since} days. Tracking trends helps us give you better "
        "suggestions.",
    )


def _first_weight_message(user: User) -> tuple[str, str]:
    return (
        "Time to log your weight",
        f"{user.display_name}, there's no weight logged yet. Tracking trends "
        "helps us give you better suggestions.",
    )


def _meal_message(user: User, days_since: int) -> tuple[str, str]:
    return (
        f"No meals logged in {days_since} days",
        f"{user.display_name}, nothing you've eaten is logged in the last "
        f"{days_since} days. A line about dinner is enough to keep the "
        "suggestions yours.",
    )


def _sleep_message(user: User, days_since: int) -> tuple[str, str]:
    return (
        f"No sleep logged in {days_since} days",
        f"{user.display_name}, it's been {days_since} days since you logged how "
        "you slept. Hours of sleep are what explain a lot of the rest.",
    )


#: Días sin entrenar a partir de los cuales se avisa, y el escalón de la escalada: se
#: vuelve a avisar a los 8, a los 12, no todos los días.
_INACTIVITY = _Absence(
    category="inactivity",
    threshold_days=4,
    base_severity=5,
    last_logged=_last_workout,
    message=_workout_message,
    first_message=_first_workout_message,
)

#: Ídem para el pesaje.
_METRIC_REMINDER = _Absence(
    category="metric_reminder",
    threshold_days=3,
    base_severity=4,
    last_logged=_last_weight,
    message=_weight_message,
    first_message=_first_weight_message,
)

#: Comer es lo más frecuente de las cuatro cosas, así que dos días sin registro ya es un
#: hueco: no es que no comieron, es que la app dejó de saber qué comen — y de ahí sale casi
#: todo lo que sugiere. Sin `first_message`: a quien nunca anotó una comida no se le pide
#: por notificación que empiece.
_MEAL_REMINDER = _Absence(
    category="meal_reminder",
    threshold_days=2,
    base_severity=4,
    last_logged=_last_meal,
    message=_meal_message,
)

#: El sueño se anota junto con el peso y es lo más fácil de que se pierda, pero también lo
#: menos accionable de las cuatro, así que arranca en el escalón más bajo.
_SLEEP_REMINDER = _Absence(
    category="sleep_reminder",
    threshold_days=3,
    base_severity=3,
    last_logged=_last_sleep,
    message=_sleep_message,
)


def _run_absence_job(absence: _Absence) -> None:
    """El driver de las cuatro ausencias por persona.

    Por cada persona activa: si volvió a registrar, se **retira** el aviso abierto y se
    sigue; si el hueco no llega al umbral, no hay nada que decir; si llega, se traduce a
    severidad y se habla, salvo que ya se haya hablado de este sujeto con esta severidad
    o peor.

    El retiro y el aviso son excluyentes por construcción, y el orden importa: si alguien
    entrena hoy, la rama del retiro es la única que corre y el sujeto queda limpio para la
    próxima vez.
    """
    if _muted(absence.category):
        return
    logger.info("Running %s notification job", absence.category)
    db = SessionLocal()
    try:
        notif_repo = NotificationRepository(db)
        notif_svc = NotificationService(db)

        for user in UserRepository(db).list_active():
            last = absence.last_logged(db, user)

            if last is None:
                if absence.first_message is None:
                    continue
                #: Nunca registró nada: no hay antigüedad que medir, así que se queda en el
                #: escalón base y no escala. El texto se arma **acá**, en la rama que sabe
                #: que `first_message` no es `None`, y no después del dedup: llevar la
                #: distinción hasta allá pedía un centinela, y un centinela numérico
                #: terminaba adentro del título — "No workouts logged in 4 days" a una
                #: cuenta creada ayer —, o sea un número inventado.
                severity = absence.base_severity
                message = absence.first_message(user)
            else:
                #: `as_utc` **convierte**; el `.replace(tzinfo=utc)` que había acá
                #: **afirmaba**. Sobre una columna `timestamptz`, que en Postgres vuelve ya
                #: aware en la timezone de la sesión, eso corría el instante tantas horas
                #: como tenga el offset: con -03:00, un pesaje de hace 2 días y 22 horas se
                #: leía como de hace 3 y el recordatorio salía un día antes del umbral.
                days_since = (local_now() - as_utc(last)).days
                if days_since < absence.threshold_days:
                    retired = notif_repo.retire_subject(
                        absence.category,
                        "user",
                        user.id,
                        household_id=user.household_id,
                        user_id=user.id,
                    )
                    if retired:
                        #: `retire_subject` hace `flush()`, no `commit()`. Si en la corrida
                        #: no se creó ninguna notificación, nadie más commitea y el retiro se
                        #: pierde en el `close()` del `finally`.
                        #:
                        #: Y se commitea **acá**, no al final: el `except` de abajo está
                        #: afuera de este `for`, así que con un solo commit al cierre una
                        #: excepción procesando a la segunda persona se llevaba también el
                        #: retiro de la primera. Es el mismo lugar en que lo hace el job de
                        #: stock. A dos personas por hogar, un commit por retiro no es un
                        #: costo.
                        db.commit()
                        logger.info(
                            "Retired %d %s notification(s) for user %d",
                            retired,
                            absence.category,
                            user.id,
                        )
                    continue
                #: Un punto más por cada tanda entera de umbrales: se avisó a los 4 días con
                #: severidad 5, a los 8 la severidad es 6 y el aviso vuelve a salir porque
                #: empeoró. Entre el 4 y el 7 no se repite.
                severity = min(
                    10, absence.base_severity + _steps_since(days_since, absence.threshold_days)
                )
                message = absence.message(user, days_since)

            if notif_repo.has_recent_for_subject(
                absence.category,
                "user",
                user.id,
                household_id=user.household_id,
                user_id=user.id,
                days=_SUBJECT_WINDOW_DAYS,
                severity=severity,
            ):
                continue

            title, body = message
            notif_svc.create(
                NotificationCreate(
                    user_id=user.id,
                    household_id=user.household_id,
                    category=absence.category,
                    title=title,
                    body=body,
                    priority=severity,
                    source_type="job",
                    related_entity_type="user",
                    related_entity_id=user.id,
                )
            )
            logger.info("Created %s notification for user %d", absence.category, user.id)

    except Exception as exc:
        #: Vía `log_job_error`: este loop es por persona, y el `INSERT` que puede fallar
        #: lleva `display_name` y el texto del aviso como parámetros atados —
        #: `IntegrityError.__str__()` los incluye, y son justamente el dato de salud de
        #: la persona que el job no tiene por qué imprimir en un log.
        log_job_error(logger, f"Error in {absence.category} job", exc)
    finally:
        db.close()


def run_inactivity_notifications() -> None:
    """Notify users who haven't logged a workout in N days."""
    _run_absence_job(_INACTIVITY)


def run_metric_reminder_notifications() -> None:
    """Remind users to log their weight if they haven't done so recently."""
    _run_absence_job(_METRIC_REMINDER)


def run_meal_reminder_notifications() -> None:
    """Remind users whose meals have stopped showing up in the log."""
    _run_absence_job(_MEAL_REMINDER)


def run_sleep_reminder_notifications() -> None:
    """Remind users who haven't logged how they slept."""
    _run_absence_job(_SLEEP_REMINDER)


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
