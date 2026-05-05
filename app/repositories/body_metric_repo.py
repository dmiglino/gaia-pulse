from datetime import datetime, timedelta

from sqlalchemy import select
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

    def get_weight_series(self, user_id: int, days: int = 30) -> list[BodyMetricLog]:
        cutoff = datetime.utcnow() - timedelta(days=days)
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
