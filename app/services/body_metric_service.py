from sqlalchemy.orm import Session

from app.models.body_metric import BodyMetricLog
from app.repositories.body_metric_repo import BodyMetricRepository
from app.schemas.body_metric import BodyMetricCreate, BodyMetricTrend


class BodyMetricService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = BodyMetricRepository(db)

    def log_metric(self, user_id: int, data: BodyMetricCreate) -> BodyMetricLog:
        metric = BodyMetricLog(
            user_id=user_id,
            timestamp=data.timestamp,
            weight_kg=data.weight_kg,
            body_fat_pct=data.body_fat_pct,
            muscle_mass_kg=data.muscle_mass_kg,
            waist_cm=data.waist_cm,
            sleep_hours=data.sleep_hours,
            notes=data.notes,
        )
        self.db.add(metric)
        self.db.flush()
        self.db.commit()
        return metric

    def get_user_metrics(self, user_id: int, limit: int = 60) -> list[BodyMetricLog]:
        return self.repo.get_user_metrics(user_id, limit=limit)

    def get_latest(self, user_id: int) -> BodyMetricLog | None:
        return self.repo.get_latest_for_user(user_id)

    def get_trend(self, user_id: int, days: int = 30) -> BodyMetricTrend:
        series = self.repo.get_weight_series(user_id, days=days)
        dates = [m.timestamp.strftime("%Y-%m-%d") for m in series]
        weights = [float(m.weight_kg) if m.weight_kg else None for m in series]

        weight_change = None
        if len(weights) >= 2:
            first = next((w for w in weights if w is not None), None)
            last = next((w for w in reversed(weights) if w is not None), None)
            if first is not None and last is not None:
                weight_change = round(last - first, 2)

        latest = self.repo.get_latest_for_user(user_id)
        return BodyMetricTrend(
            user_id=user_id,
            dates=dates,
            weights=weights,
            latest_weight=float(latest.weight_kg) if latest and latest.weight_kg else None,
            weight_change_30d=weight_change,
            latest_body_fat=float(latest.body_fat_pct) if latest and latest.body_fat_pct else None,
            latest_waist=float(latest.waist_cm) if latest and latest.waist_cm else None,
        )

    def delete_metric(self, metric_id: int, user_id: int) -> bool:
        metric = self.repo.get(metric_id)
        if not metric or metric.user_id != user_id:
            return False
        self.repo.delete(metric)
        self.db.commit()
        return True
