from fastapi import APIRouter, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.pantry_service import PantryService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_MOVEMENT_TYPES = {"adjustment", "purchase", "consumption", "discard"}


def _back_to_pantry(message: str, category: str) -> RedirectResponse:
    """Redirect to the pantry carrying a one-shot message (non-HTMX fallback)."""
    response = RedirectResponse(url="/pantry", status_code=302)
    set_flash(response, message, category)
    return response


#: Los nueve valores que `FoodItem.category` guarda en la base (`seed.py`). Las
#: pestañas de la v1 mandaban rótulos en inglés capitalizados que no coincidían con
#: ninguno, así que el filtro de categoría nunca devolvía nada.
FOOD_CATEGORIES = [
    "vegetable",
    "fruit",
    "protein",
    "grain",
    "dairy",
    "fat",
    "beverage",
    "processed",
    "other",
]


@router.get("/", response_class=HTMLResponse)
def pantry_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    search: str | None = Query(None),
    category: str | None = Query(None),
    low: str | None = Query(None),
) -> HTMLResponse:
    """Pantry stock, filtered by name, category and low-stock state.

    `low` llega como string porque un checkbox no marcado no manda nada y uno
    marcado manda `low=1`: con `bool` FastAPI contestaría 422 a un `?low=` vacío.
    """
    svc = PantryService(db)
    low_only = bool(low)
    ctx = get_template_context(request, db, current_user)
    ctx["stock_items"] = svc.get_stock(
        current_user.household_id, search=search, category=category, low_only=low_only
    )
    ctx["filters_active"] = bool(search or category or low_only)

    # La grilla es lo único que se intercambia, y no muestra los contadores del
    # encabezado: pedirlos en cada tecla del buscador era recorrer la despensa
    # completa para descartar el resultado.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("pantry/partials/stock_grid.html", ctx)

    summary = svc.get_stock_summary(current_user.household_id)
    ctx["stock_total"] = summary["total"]
    ctx["low_stock_count"] = summary["low"]
    ctx["search"] = search or ""
    ctx["category"] = category or ""
    ctx["low_only"] = low_only
    ctx["categories"] = FOOD_CATEGORIES
    return templates.TemplateResponse("pantry/index.html", ctx)


@router.post("/{stock_id}/adjust", response_class=HTMLResponse)
def pantry_adjust(
    request: Request,
    stock_id: int,
    current_user: CurrentUser,
    db: DB,
    quantity: float = Form(...),
    movement_type: str = Form(default="adjustment"),
) -> Response:
    """Adjust one pantry item from the stock grid and swap its card back in."""
    is_htmx = bool(request.headers.get("HX-Request"))

    if movement_type not in _VALID_MOVEMENT_TYPES or quantity < 0:
        if is_htmx:
            return HTMLResponse(_("Invalid stock adjustment."), status_code=400)
        return _back_to_pantry(_("Invalid stock adjustment."), "error")

    svc = PantryService(db)
    item = svc.adjust_stock_by_id(
        household_id=current_user.household_id,
        user_id=current_user.id,
        stock_id=stock_id,
        quantity=quantity,
        movement_type=movement_type,
    )
    if item is None:
        if is_htmx:
            return HTMLResponse(_("Pantry item not found."), status_code=404)
        return _back_to_pantry(_("Pantry item not found."), "error")

    if not is_htmx:
        return _back_to_pantry(_("Stock updated."), "success")

    ctx = get_template_context(request, db, current_user)
    ctx["item"] = item
    summary = svc.get_stock_summary(current_user.household_id)
    ctx["stock_total"] = summary["total"]
    ctx["low_stock_count"] = summary["low"]
    ctx["oob"] = True
    return templates.TemplateResponse("pantry/partials/stock_card_swap.html", ctx)


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
