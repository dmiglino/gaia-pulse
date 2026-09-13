from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.meal_service import MealService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, query_date, query_int, templates

router = APIRouter()

# Cuántas comidas trae una tanda. La v1 pedía 20 y ofrecía un "cargar más" que
# apuntaba a `?page=`, un parámetro que la ruta no tiene, contra atributos
# (`has_next`, `next_num`) que una `list` no tiene: el botón no se renderizaba nunca,
# así que a partir de la comida 21 no había forma de ver más nada.
PAGE_SIZE = 20


@router.get("/", response_class=HTMLResponse)
def meals_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: str | None = Query(None),
    date: str | None = Query(None),
    offset: int = Query(0, ge=0),
) -> HTMLResponse:
    """La lista de comidas, filtrable por día y por persona.

    Responde el parcial cuando la llamada viene de HTMX y la página completa cuando
    no: los filtros y el "cargar más" intercambian solo la lista, pero la misma URL
    tiene que servir para compartirla o recargarla (`hx-push-url`).
    """
    svc = MealService(db)
    filter_user_id = query_int(user_id)
    filter_date = query_date(date)

    ctx = get_template_context(request, db, current_user)
    # Se pide una fila más que la página: con eso alcanza para saber si hay algo más
    # que mostrar, sin un `COUNT(*)` extra sobre la tabla entera.
    page = svc.get_meals(
        current_user.household_id,
        limit=PAGE_SIZE + 1,
        offset=offset,
        user_id=filter_user_id,
        on_date=filter_date,
    )
    ctx["meal_events"] = page[:PAGE_SIZE]
    ctx["has_more"] = len(page) > PAGE_SIZE
    ctx["offset"] = offset
    ctx["next_offset"] = offset + PAGE_SIZE
    ctx["filter_user_id"] = filter_user_id
    ctx["filter_date"] = filter_date.isoformat() if filter_date else ""
    ctx["filters_active"] = bool(filter_user_id or filter_date)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("meals/partials/list.html", ctx)
    return templates.TemplateResponse("meals/index.html", ctx)


@router.get("/{meal_id}", response_class=HTMLResponse)
def meal_detail(
    request: Request,
    meal_id: int,
    current_user: CurrentUser,
    db: DB,
) -> Response:
    svc = MealService(db)
    meal = svc.get_meal(meal_id)
    # Un id de otro hogar y un id inexistente se responden igual, a propósito: la
    # diferencia le contaría a quien prueba ids que esa comida existe.
    if not meal or meal.household_id != current_user.household_id:
        # La v1 devolvía `meals/index.html` con un `ctx["error"]` que esa plantilla
        # nunca renderizó, y sin `meal_events`: la pantalla decía "todavía no hay
        # comidas registradas" incluso con el diario lleno. El mensaje va por flash,
        # que es el mecanismo que el resto de la app ya usa.
        response = RedirectResponse(url="/meals", status_code=302)
        set_flash(response, _("That meal is not available."), "error")
        return response

    ctx = get_template_context(request, db, current_user)
    ctx["meal"] = meal
    return templates.TemplateResponse("meals/detail.html", ctx)
