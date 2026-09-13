"""Dashboard — los gráficos de una persona del hogar."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
from app.services.dashboard_service import DashboardService
from app.web.helpers import get_template_context, query_int, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: str | None = Query(None),
) -> HTMLResponse:
    """Los cinco gráficos, para la persona seleccionada.

    `?user_id=` llegaba como `int | None` y se usaba sin validar (`user_id or
    current_user.id`). Dos problemas: el `int | None` hace que FastAPI responda 422 en
    JSON desde una ruta de página, y el id sin validar iba derecho a
    `get_weight_series`, que filtra **solo** por usuario — o sea que el id de cualquier
    persona de la base devolvía su serie de peso de 30 días. Los otros cuatro gráficos
    ya arrancaban del hogar. Es la misma validación que `/body-metrics/`: el id se
    resuelve contra el padrón del hogar y cualquier otra cosa cae en quien pregunta.
    """
    ctx = get_template_context(request, db, current_user)
    requested = query_int(user_id)
    selected = next((u for u in ctx["users"] if u.id == requested), None) or current_user
    ctx["chart_data"] = DashboardService(db).get_user_dashboard_data(
        selected.id, current_user.household_id
    )
    ctx["selected_user_id"] = selected.id
    return templates.TemplateResponse("dashboard/index.html", ctx)
