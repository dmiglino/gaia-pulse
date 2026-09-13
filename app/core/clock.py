"""El reloj de la app: una sola definición de "ahora" en hora local.

Por qué existe: `settings.timezone` estaba en la config, en el README y en
`.env.example`, y el único lugar que lo leía era `BackgroundScheduler(timezone=...)`
— donde, sin un `CronTrigger`, no tenía ningún efecto observable. Mientras tanto
`home.html` llamaba a `now()`, un global de Jinja que **nadie había registrado**,
así que el guardia `{% if now is defined %}` caía siempre al `else`: el saludo
decía "buenas tardes" a las 7 de la mañana y la fecha decía literalmente "Hoy".

Todo lo que el usuario lee como "hoy", "esta mañana" o "hace tres días" se mide
acá, y también *cuándo* la app se permite hablar: `is_quiet_hours()` es la franja
en la que no genera notificaciones. El resto de la app no debería volver a llamar
a `datetime.now()` sin timezone.
"""

import logging
from datetime import UTC, date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _zone(name: str) -> ZoneInfo:
    """Resuelve una timezone por nombre, cayendo a UTC si el sistema no la tiene.

    Un `TIMEZONE` mal escrito en el entorno no debe tumbar cada página: el reloj
    se degrada a UTC y lo deja anotado en el log una sola vez por valor, porque
    `lru_cache` no vuelve a entrar acá para el mismo nombre.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Timezone '%s' desconocida; el reloj usa UTC.", name)
        return ZoneInfo("UTC")


def household_tz() -> ZoneInfo:
    """La timezone configurada para el hogar."""
    return _zone(get_settings().timezone)


def local_now() -> datetime:
    """Ahora, aware, en la timezone del hogar."""
    return datetime.now(household_tz())


def local_today() -> date:
    """El día de hoy según el hogar, no según UTC."""
    return local_now().date()


def to_local(moment: datetime) -> datetime:
    """Lleva un instante a hora local.

    Un datetime naive se **interpreta** como UTC (es lo que guardan las columnas
    del esquema) y luego se convierte, en vez de que se le afirme la timezone
    local encima.
    """
    return as_utc(moment).astimezone(household_tz())


def as_utc(moment: datetime) -> datetime:
    """El mismo instante, aware, en UTC.

    Es la mitad que faltaba de `to_local`, y existe porque el patrón que había
    repartido por el motor —  `columna.replace(tzinfo=timezone.utc)` — no
    convierte: **afirma**. Sobre una columna `timestamptz`, que en Postgres vuelve
    ya aware y en la timezone de la sesión, eso corre el instante tantas horas como
    tenga el offset: "hace 1 día" se leía como "hace 2 días" o "en 3 horas" según
    de qué lado del meridiano estuviera el servidor, y de ahí salían recordatorios
    de pesaje que se disparaban antes de tiempo y ventanas de recuperación
    muscular que se cerraban solas.

    Un naive sí se interpreta como UTC, porque es lo que guarda SQLite en los
    tests y lo que escribían los repositorios con `datetime.utcnow()`.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """El primer y el último instante de *day* **en hora local**, aware, en UTC.

    Los repositorios armaban los límites con `datetime.combine(day, min/max)` sin
    timezone y los comparaban contra columnas `timestamptz`. Un naive así no es un
    día local: Postgres lo interpreta en la timezone de la sesión y SQLite lo
    compara como texto, así que "hoy" terminaba siendo el día UTC. Con el proceso
    en UTC — que es como corre en el contenedor — una comida de las 22:00 de acá
    caía en el "hoy" de mañana: el contador del Home la perdía y el filtro por día
    de `/meals` la mostraba en el día equivocado.

    El fin es inclusivo (`23:59:59.999999`) porque es lo que esperan los `<=` que
    ya están escritos en las consultas.
    """
    zone = household_tz()
    return (
        as_utc(datetime.combine(day, time.min, tzinfo=zone)),
        as_utc(datetime.combine(day, time.max, tzinfo=zone)),
    )


def is_quiet_hours(moment: datetime | None = None) -> bool:
    """¿Estamos en la franja en la que la app no avisa nada?

    Nada impedía una notificación a las 4 de la mañana: los cuatro jobs eran
    `IntervalTrigger` sin `start_date`, así que la primera corrida caía en
    `arranque + intervalo` y desde ahí quedaban clavados a esa hora para siempre.
    Un reinicio a las 03:00 dejaba la app avisando a las 03:00 todos los días.

    El horario se define por horas enteras locales (`QUIET_HOURS_START` /
    `QUIET_HOURS_END`) y la franja **cruza la medianoche** cuando el inicio es
    posterior al fin, que es el caso normal (22 → 8). Con inicio igual a fin la
    franja está vacía y la app puede hablar a cualquier hora: es la forma de
    apagar el silencio sin agregar un flag aparte.

    Un `moment` naive se interpreta como UTC, igual que en `to_local`.
    """
    settings = get_settings()
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start == end:
        return False
    hour = (to_local(moment) if moment is not None else local_now()).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end
