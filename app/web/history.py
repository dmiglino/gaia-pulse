"""History — un timeline por tipo de registro, con filtro de persona y paginación."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.pantry_service import PantryService
from app.services.workout_service import WorkoutService
from app.web.helpers import get_template_context, query_int, templates

router = APIRouter()

#: Tamaño de página. La plantilla lo necesita para decidir si dibuja "Older →"
#: (una página incompleta es la última), así que viaja en el contexto en lugar de
#: quedar como un `30` literal repetido en los dos lados.
PAGE_SIZE = 30

#: Las cuatro pestañas que la pantalla sabe renderizar. Un `?tab=` que no esté acá
#: caía en un `else` que dejaba `events` vacío: la pantalla decía "todavía no hay
#: registros" —con la base llena— por un error de tipeo en la URL.
TABS = ("meals", "workouts", "body_metrics", "pantry")


@router.get("/", response_class=HTMLResponse)
def history_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    tab: str = Query("meals"),
    user_id: str | None = Query(None),
    offset: str | None = Query(None),
) -> HTMLResponse:
    """El timeline de una pestaña, filtrado por persona y paginado.

    `user_id` y `offset` entran como `str | None` y pasan por `query_int` porque con
    `int | None` FastAPI responde 422 —en JSON, en una ruta de página— ante un
    `?user_id=` vacío o basura, y las URLs de este timeline se comparten y se editan
    a mano.

    `user_id` se valida contra los usuarios del hogar. En comidas y entrenamientos un
    id ajeno solo daba lista vacía (esas consultas ya filtran por `household_id`),
    pero el tab de métricas corporales lo pasaba derecho a `get_user_metrics`, así que
    devolvía el peso, la grasa corporal y las horas de sueño de cualquier persona de
    la base. Todo dato personal se filtra por el hogar de quien pregunta.
    """
    ctx = get_template_context(request, db, current_user)

    active_tab = tab if tab in TABS else TABS[0]
    requested = query_int(user_id)
    selected = next((u for u in ctx["users"] if u.id == requested), None)
    # `max(0, ...)`: un `?offset=-30` escrito a mano llegaba tal cual al `OFFSET` de
    # la consulta, que en Postgres es un error de sintaxis y en SQLite se ignora.
    page_offset = max(0, query_int(offset) or 0)

    ctx["active_tab"] = active_tab
    ctx["filter_user_id"] = selected.id if selected else None
    ctx["offset"] = page_offset
    ctx["page_size"] = PAGE_SIZE

    uid = selected.id if selected else None  # None = todo el hogar

    if active_tab == "meals":
        ctx["events"] = MealService(db).get_meals(
            current_user.household_id, limit=PAGE_SIZE, offset=page_offset, user_id=uid
        )
    elif active_tab == "workouts":
        ctx["events"] = WorkoutService(db).get_sessions(
            current_user.household_id, limit=PAGE_SIZE, offset=page_offset, user_id=uid
        )
    elif active_tab == "body_metrics":
        # Las métricas son por persona y no por hogar: sin filtro elegido, las de
        # quien está mirando.
        ctx["events"] = BodyMetricService(db).get_user_metrics(
            uid or current_user.id, limit=PAGE_SIZE, offset=page_offset
        )
    else:
        # La despensa es del hogar: no tiene columna de persona para filtrar, y por
        # eso la plantilla esconde el selector en esta pestaña.
        ctx["events"] = PantryService(db).get_movements(
            current_user.household_id, limit=PAGE_SIZE, offset=page_offset
        )

    return templates.TemplateResponse("history/index.html", ctx)
