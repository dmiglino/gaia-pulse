"""Tests for body metric logging and trends."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.body_metric import BodyMetricCreate
from app.services.body_metric_service import BodyMetricService


def dt(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


class TestBodyMetricLogging:
    def test_log_weight(self, db: Session, diego: User) -> None:
        svc = BodyMetricService(db)
        metric = svc.log_metric(diego.id, BodyMetricCreate(
            timestamp=datetime.now(timezone.utc),
            weight_kg=82.5,
        ))
        assert metric.id is not None
        assert float(metric.weight_kg) == pytest.approx(82.5)

    def test_log_full_metric(self, db: Session, diego: User) -> None:
        svc = BodyMetricService(db)
        metric = svc.log_metric(diego.id, BodyMetricCreate(
            timestamp=datetime.now(timezone.utc),
            weight_kg=82.5,
            body_fat_pct=18.5,
            waist_cm=88.0,
            sleep_hours=7.5,
        ))
        assert float(metric.body_fat_pct) == pytest.approx(18.5)
        assert float(metric.waist_cm) == pytest.approx(88.0)

    def test_get_latest(self, db: Session, diego: User) -> None:
        svc = BodyMetricService(db)
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(5), weight_kg=83.0))
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(2), weight_kg=82.5))
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(0), weight_kg=82.0))

        latest = svc.get_latest(diego.id)
        assert float(latest.weight_kg) == pytest.approx(82.0)

    def test_weight_trend_change(self, db: Session, diego: User) -> None:
        svc = BodyMetricService(db)
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(25), weight_kg=85.0))
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(15), weight_kg=84.0))
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(5), weight_kg=83.0))

        trend = svc.get_trend(diego.id, days=30)
        assert trend.weight_change_30d is not None
        assert trend.weight_change_30d < 0  # lost weight
        assert trend.latest_weight == pytest.approx(83.0)

    def test_metrics_are_user_scoped(
        self, db: Session, diego: User, rocio: User
    ) -> None:
        svc = BodyMetricService(db)
        svc.log_metric(diego.id, BodyMetricCreate(timestamp=dt(0), weight_kg=82.0))
        svc.log_metric(rocio.id, BodyMetricCreate(timestamp=dt(0), weight_kg=60.0))

        diego_latest = svc.get_latest(diego.id)
        rocio_latest = svc.get_latest(rocio.id)

        assert float(diego_latest.weight_kg) == pytest.approx(82.0)
        assert float(rocio_latest.weight_kg) == pytest.approx(60.0)
        assert diego_latest.user_id == diego.id
        assert rocio_latest.user_id == rocio.id
