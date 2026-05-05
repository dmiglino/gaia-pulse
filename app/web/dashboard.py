from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.dashboard_service import DashboardService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: int | None = Query(None),
) -> HTMLResponse:
    svc = DashboardService(db)
    ctx = get_template_context(request, db, current_user)
    uid = user_id or current_user.id
    ctx["chart_data"] = svc.get_user_dashboard_data(uid, current_user.household_id)
    ctx["selected_user_id"] = uid
    return templates.TemplateResponse("dashboard/index.html", ctx)
