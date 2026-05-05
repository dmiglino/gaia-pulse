from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.pantry_service import PantryService
from app.web.helpers import get_template_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def pantry_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    search: str | None = Query(None),
    category: str | None = Query(None),
) -> HTMLResponse:
    svc = PantryService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["stock_items"] = svc.get_stock(current_user.household_id, search=search, category=category)
    ctx["low_stock_items"] = svc.get_low_stock(current_user.household_id)
    ctx["low_stock_count"] = len(ctx["low_stock_items"])
    ctx["search"] = search or ""
    ctx["category"] = category or ""
    ctx["categories"] = [
        "vegetable", "fruit", "protein", "grain", "dairy", "fat", "beverage", "processed", "other"
    ]

    # HTMX partial response
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("pantry/partials/stock_grid.html", ctx)
    return templates.TemplateResponse("pantry/index.html", ctx)


@router.get("/movements", response_class=HTMLResponse)
def pantry_movements(
    request: Request,
    current_user: CurrentUser,
    db: DB,
) -> HTMLResponse:
    svc = PantryService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["movements"] = svc.get_movements(current_user.household_id, limit=50)
    return templates.TemplateResponse("pantry/movements.html", ctx)
