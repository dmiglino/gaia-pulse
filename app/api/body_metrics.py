from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import DB, CurrentUser
from app.schemas.body_metric import BodyMetricCreate, BodyMetricRead, BodyMetricTrend
from app.services.body_metric_service import BodyMetricService

router = APIRouter()


@router.get("/", response_model=list[BodyMetricRead])
def get_metrics(
    current_user: CurrentUser,
    db: DB,
    limit: int = Query(60, le=365),
    user_id: int | None = Query(None),
) -> list[BodyMetricRead]:
    svc = BodyMetricService(db)
    uid = user_id or current_user.id
    return [BodyMetricRead.model_validate(m) for m in svc.get_user_metrics(uid, limit=limit)]


@router.post("/", response_model=BodyMetricRead, status_code=201)
def log_metric(data: BodyMetricCreate, current_user: CurrentUser, db: DB) -> BodyMetricRead:
    svc = BodyMetricService(db)
    metric = svc.log_metric(current_user.id, data)
    return BodyMetricRead.model_validate(metric)


@router.get("/trend", response_model=BodyMetricTrend)
def get_trend(
    current_user: CurrentUser,
    db: DB,
    days: int = Query(30, le=365),
    user_id: int | None = Query(None),
) -> BodyMetricTrend:
    svc = BodyMetricService(db)
    uid = user_id or current_user.id
    return svc.get_trend(uid, days=days)


@router.delete("/{metric_id}", status_code=204)
def delete_metric(metric_id: int, current_user: CurrentUser, db: DB) -> None:
    svc = BodyMetricService(db)
    if not svc.delete_metric(metric_id, current_user.id):
        raise HTTPException(status_code=404, detail="Metric not found")
