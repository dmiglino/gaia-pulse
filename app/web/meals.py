from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.meal_service import MealService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def meals_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: int | None = Query(None),
    offset: int = Query(0),
) -> HTMLResponse:
    svc = MealService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["meal_events"] = svc.get_meals(
        current_user.household_id, limit=20, offset=offset, user_id=user_id
    )
    ctx["filter_user_id"] = user_id
    ctx["offset"] = offset
    return templates.TemplateResponse("meals/index.html", ctx)


@router.get("/{meal_id}", response_class=HTMLResponse)
def meal_detail(
    request: Request,
    meal_id: int,
    current_user: CurrentUser,
    db: DB,
) -> HTMLResponse:
    svc = MealService(db)
    ctx = get_template_context(request, db, current_user)
    meal = svc.get_meal(meal_id)
    if not meal or meal.household_id != current_user.household_id:
        ctx["error"] = _("Meal not found.")
        return templates.TemplateResponse("meals/index.html", ctx, status_code=404)
    ctx["meal"] = meal
    return templates.TemplateResponse("meals/detail.html", ctx)
