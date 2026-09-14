from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.body_metric_service import BodyMetricService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, query_int, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def body_metrics_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: str | None = Query(None),
) -> HTMLResponse:
    """Body metrics for one member of the household, chosen by the person tabs.

    `user_id` se valida contra los usuarios del hogar antes de tocar el servicio:
    la v1 lo pasaba directo a `get_user_metrics`, así que `?user_id=` con el id de
    cualquier otra persona de la base devolvía **sus** pesajes, su grasa corporal y
    sus horas de sueño. Todo dato personal se filtra por el hogar de quien pregunta.

    Se trae una sola persona por request y no las dos: la v1 calculaba tendencia y
    último registro de todos los miembros (dos consultas por cabeza) y la plantilla
    no leía ninguno de los dos — mostraba "—" en las cuatro tarjetas.
    """
    svc = BodyMetricService(db)
    ctx = get_template_context(request, db, current_user)

    users = ctx["users"]
    requested = query_int(user_id)
    selected = next((u for u in users if u.id == requested), None) or current_user

    ctx["selected_user"] = selected
    ctx["latest"] = svc.get_latest(selected.id)
    ctx["trend"] = svc.get_trend(selected.id, days=30)
    ctx["metrics"] = svc.get_user_metrics(selected.id, limit=30)
    return templates.TemplateResponse("body_metrics/index.html", ctx)


@router.post("/{metric_id}/delete")
def body_metric_delete(
    metric_id: int,
    current_user: CurrentUser,
    db: DB,
) -> RedirectResponse:
    """Borra un registro de métricas corporales.

    `BodyMetricService.delete_metric` ya scopea por `user_id` (son datos
    personales, a diferencia de comidas y entrenamientos): nadie borra un
    pesaje ajeno aunque lo esté mirando desde la pestaña de esa persona.
    """
    svc = BodyMetricService(db)
    if not svc.delete_metric(metric_id, current_user.id):
        response = RedirectResponse(url="/body-metrics", status_code=302)
        set_flash(response, _("That log entry is not available."), "error")
        return response

    response = RedirectResponse(url="/body-metrics", status_code=302)
    set_flash(response, _("Log entry deleted."), "success")
    return response
