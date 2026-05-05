from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.repositories.user_repo import UserRepository
from app.services.body_metric_service import BodyMetricService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def body_metrics_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: int | None = Query(None),
) -> HTMLResponse:
    svc = BodyMetricService(db)
    ctx = get_template_context(request, db, current_user)

    users = UserRepository(db).get_household_users(current_user.household_id)
    trends = {}
    latest = {}
    for u in users:
        trends[u.id] = svc.get_trend(u.id, days=30)
        latest[u.id] = svc.get_latest(u.id)

    ctx["trends"] = trends
    ctx["latest_metrics"] = latest
    ctx["filter_user_id"] = user_id or current_user.id
    ctx["metrics"] = svc.get_user_metrics(user_id or current_user.id, limit=30)
    return templates.TemplateResponse("body_metrics/index.html", ctx)
