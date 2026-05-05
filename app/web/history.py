from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
from app.services.workout_service import WorkoutService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def history_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    tab: str = Query("meals"),
    user_id: int | None = Query(None),
    offset: int = Query(0),
) -> HTMLResponse:
    ctx = get_template_context(request, db, current_user)
    ctx["active_tab"] = tab
    ctx["filter_user_id"] = user_id
    ctx["offset"] = offset
    uid = user_id or None  # None = all users (household)

    if tab == "meals":
        ctx["events"] = MealService(db).get_meals(
            current_user.household_id, limit=30, offset=offset, user_id=uid
        )
    elif tab == "workouts":
        ctx["events"] = WorkoutService(db).get_sessions(
            current_user.household_id, limit=30, offset=offset, user_id=uid
        )
    elif tab == "body_metrics":
        target_uid = user_id or current_user.id
        ctx["events"] = BodyMetricService(db).get_user_metrics(target_uid, limit=30)
    elif tab == "pantry":
        ctx["events"] = PantryService(db).get_movements(
            current_user.household_id, limit=30, offset=offset
        )
    else:
        ctx["events"] = []

    return templates.TemplateResponse("history/index.html", ctx)
