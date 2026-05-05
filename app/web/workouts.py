from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.workout_service import WorkoutService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def workouts_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: int | None = Query(None),
    offset: int = Query(0),
) -> HTMLResponse:
    svc = WorkoutService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["workout_sessions"] = svc.get_sessions(
        current_user.household_id, limit=20, offset=offset, user_id=user_id
    )
    ctx["filter_user_id"] = user_id
    ctx["offset"] = offset
    return templates.TemplateResponse("workouts/index.html", ctx)
