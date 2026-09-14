from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.workout_service import WorkoutService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, query_date, query_int, templates

router = APIRouter()

# Igual que en las comidas: el "cargar más" de la v1 apuntaba a `?page=` y leía
# `has_next`/`next_num` sobre una `list`, así que no se renderizaba nunca.
PAGE_SIZE = 20


@router.get("/", response_class=HTMLResponse)
def workouts_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    user_id: str | None = Query(None),
    date: str | None = Query(None),
    offset: int = Query(0, ge=0),
) -> HTMLResponse:
    """La lista de entrenamientos, filtrable por día y por persona.

    Devuelve el parcial cuando la llamada viene de HTMX y la página completa cuando
    no, para que la misma URL sirva para compartirla o recargarla (`hx-push-url`).
    """
    svc = WorkoutService(db)
    filter_user_id = query_int(user_id)
    filter_date = query_date(date)

    ctx = get_template_context(request, db, current_user)
    page = svc.get_sessions(
        current_user.household_id,
        limit=PAGE_SIZE + 1,
        offset=offset,
        user_id=filter_user_id,
        on_date=filter_date,
    )
    ctx["workout_sessions"] = page[:PAGE_SIZE]
    ctx["has_more"] = len(page) > PAGE_SIZE
    ctx["offset"] = offset
    ctx["next_offset"] = offset + PAGE_SIZE
    ctx["filter_user_id"] = filter_user_id
    ctx["filter_date"] = filter_date.isoformat() if filter_date else ""
    ctx["filters_active"] = bool(filter_user_id or filter_date)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("workouts/partials/list.html", ctx)
    return templates.TemplateResponse("workouts/index.html", ctx)


@router.post("/{session_id}/delete")
def workout_delete(
    session_id: int,
    current_user: CurrentUser,
    db: DB,
) -> RedirectResponse:
    """Borra un entrenamiento del hogar. Mismo chequeo de pertenencia que
    `DELETE /api/workouts/{id}`: un id de otro hogar se trata igual que uno
    inexistente."""
    svc = WorkoutService(db)
    session = svc.get_session(session_id)
    if not session or session.household_id != current_user.household_id:
        response = RedirectResponse(url="/workouts", status_code=302)
        set_flash(response, _("That workout is not available."), "error")
        return response

    svc.delete_session(session_id)
    response = RedirectResponse(url="/workouts", status_code=302)
    set_flash(response, _("Workout deleted."), "success")
    return response
