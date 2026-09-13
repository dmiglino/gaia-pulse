from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.body_metric import BodyMetricLog
from app.repositories.base import BaseRepository


class BodyMetricRepository(BaseRepository[BodyMetricLog]):
    def __init__(self, db: Session) -> None:
        super().__init__(BodyMetricLog, db)

    def get_user_metrics(
        self,
        user_id: int,
        limit: int = 60,
        offset: int = 0,
    ) -> list[BodyMetricLog]:
        stmt = (
            select(BodyMetricLog)
            .where(BodyMetricLog.user_id == user_id)
            .order_by(BodyMetricLog.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.scalars(stmt).all())

    def get_latest_for_user(self, user_id: int) -> BodyMetricLog | None:
        stmt = (
            select(BodyMetricLog)
            .where(BodyMetricLog.user_id == user_id)
            .order_by(BodyMetricLog.timestamp.desc())
            .limit(1)
        )
        return self.db.scalar(stmt)

    def get_last_weight_at(self, user_id: int) -> datetime | None:
        """Cuándo se pesó por última vez, o `None` si nunca.

        El espejo de `get_last_sleep_at`, y por el mismo motivo: `weight_kg` también es
        opcional. `get_latest_for_user` trae la última fila cualquiera sea su contenido, o
        sea que "dormí 7 horas" —que el parser acepta sin peso
        (`app/nlp/rules.py`, y `BodyMetricService` la escribe con `weight_kg=NULL`)— pasaba
        por pesaje. Antes eso solo atrasaba el recordatorio; con el retiro por sujeto de la
        4.3 lo **borra**, y con él la marca de agua: quien anota sueño cada dos días no
        volvía a recibir el aviso del peso nunca, y en silencio.

        `get_weight_series` ya filtraba así unas líneas más abajo; esto es la misma regla
        para el instante suelto.
        """
        stmt = select(func.max(BodyMetricLog.timestamp)).where(
            BodyMetricLog.user_id == user_id,
            BodyMetricLog.weight_kg.is_not(None),
        )
        return self.db.scalar(stmt)

    def get_last_sleep_at(self, user_id: int) -> datetime | None:
        """Cuándo anotó horas de sueño por última vez, o `None` si nunca.

        No sirve `get_latest_for_user`: `sleep_hours` es una columna opcional del mismo
        registro que el peso, así que la última medición de alguien que se pesa todos los
        días casi nunca trae sueño. Preguntar por la última fila daría "anotó sueño hoy"
        para alguien que no lo anota desde marzo.

        Un `max()` en la base y no la fila entera: el job de ausencia solo necesita el
        instante para medir el hueco.
        """
        stmt = select(func.max(BodyMetricLog.timestamp)).where(
            BodyMetricLog.user_id == user_id,
            BodyMetricLog.sleep_hours.is_not(None),
        )
        return self.db.scalar(stmt)

    def get_weight_series(self, user_id: int, days: int = 30) -> list[BodyMetricLog]:
        #: Aware. `timestamp` es `timestamptz`, y un cutoff naive lo interpreta
        #: Postgres en la timezone de la *sesión*, no en UTC: la ventana de
        #: "últimos 30 días" se corría el offset del servidor.
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(BodyMetricLog)
            .where(
                BodyMetricLog.user_id == user_id,
                BodyMetricLog.timestamp >= cutoff,
                BodyMetricLog.weight_kg.isnot(None),
            )
            .order_by(BodyMetricLog.timestamp.asc())
        )
        return list(self.db.scalars(stmt).all())
