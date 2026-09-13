"""El reloj de la app: una sola definición de "ahora" en hora local.

Por qué existe: `settings.timezone` estaba en la config, en el README y en
`.env.example`, y el único lugar que lo leía era `BackgroundScheduler(timezone=...)`
— donde, sin un `CronTrigger`, no tenía ningún efecto observable. Mientras tanto
`home.html` llamaba a `now()`, un global de Jinja que **nadie había registrado**,
así que el guardia `{% if now is defined %}` caía siempre al `else`: el saludo
decía "buenas tardes" a las 7 de la mañana y la fecha decía literalmente "Hoy".

Todo lo que el usuario lee como "hoy", "esta mañana" o "hace tres días" se mide
acá. La Fase 4 agrega `is_quiet_hours()` sobre este mismo módulo y pasa el
scheduler a `CronTrigger`; el resto de la app no debería volver a llamar a
`datetime.now()` sin timezone.
"""

import logging
from datetime import UTC, date, datetime
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
    local encima — que es el bug que tiene hoy `notification_jobs.py`.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(household_tz())
