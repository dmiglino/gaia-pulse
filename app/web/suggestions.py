from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.schemas.suggestion import RecommendationPreferenceCreate, SuggestionFeedback
from app.services.suggestion_service import SuggestionService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_FEEDBACK_STATUSES = {"accepted", "rejected", "snoozed", "dismissed"}
_VALID_PREFERENCE_SIGNALS = {"likes", "dislikes", "impossible", "possible_sometimes", "avoid", "preferred"}


def _redirect_with_flash(url: str, message: str, category: str) -> RedirectResponse:
    """Redirect to *url* carrying a one-shot message (non-HTMX fallback)."""
    response = RedirectResponse(url=url, status_code=302)
    set_flash(response, message, category)
    return response


#: Con la barra final. Sin ella cada redirect cobraba un 307 de `redirect_slashes`
#: antes de llegar a la ruta real, en el camino de vuelta de cada respuesta a una
#: sugerencia y de cada preferencia guardada sin JS.
def _back_to_suggestions(message: str, category: str) -> RedirectResponse:
    return _redirect_with_flash("/suggestions/", message, category)


def _back_to_profile(message: str, category: str) -> RedirectResponse:
    return _redirect_with_flash("/profile/", message, category)


def _invalid(
    request: Request,
    message: str,
    status_code: int = 422,
    back: str = "/suggestions/",
) -> Response:
    """Contestar una request que no se puede atender, sin devolver una página entera.

    Para HTMX, un fragmento con el motivo y el 4xx correspondiente: HTMX no intercambia
    respuestas 4xx, así que la tarjeta queda como estaba en vez de desaparecer
    reemplazada por un error. Sin JS, el redirect de siempre con el mensaje en el flash
    — que además es mejor que una página de error para una tarjeta que ya no está.

    Lo usan las tres salidas de fallo del módulo (status inválido, sugerencia que no
    existe o no es tuya, señal de preferencia inválida) para que ninguna mande un
    documento entero hacia un target que es una tarjeta.

    Que la tarjeta no desaparezca lo sigue sosteniendo el default de HTMX de no
    intercambiar 4xx — esto no lo cambia. Lo que cambia es qué se manda: un cuerpo
    proporcional al fallo en lugar de `suggestions/index.html` o `errors/404.html`
    enteras, que es lo que antes viajaba por el cable y quedaba a un `hx-swap="none"`
    de distancia de aparecer anidado adentro del `<body>`.
    """
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/flash_messages.html",
            {"request": request, "messages": [{"type": "error", "text": message}]},
            status_code=status_code,
        )
    return _redirect_with_flash(back, message, "error")


@router.get("/", response_class=HTMLResponse)
def suggestions_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    svc = SuggestionService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["suggestions"] = svc.get_pending(current_user.id, current_user.household_id)
    ctx["preferences"] = svc.get_user_preferences(current_user.id)
    return templates.TemplateResponse("suggestions/index.html", ctx)


@router.post("/generate", response_class=HTMLResponse)
def generate_suggestions(request: Request, current_user: CurrentUser, db: DB) -> Response:
    """Run the recommendation engine on demand and swap the list back in."""
    svc = SuggestionService(db)
    svc.generate_for_user(current_user, limit=10)

    if not request.headers.get("HX-Request"):
        return _back_to_suggestions(_("Suggestions refreshed."), "success")

    ctx = get_template_context(request, db, current_user)
    ctx["suggestions"] = svc.get_pending(current_user.id, current_user.household_id)
    return templates.TemplateResponse("suggestions/partials/list.html", ctx)


@router.post("/{suggestion_id}/feedback")
def suggestion_feedback(
    request: Request,
    suggestion_id: int,
    current_user: CurrentUser,
    db: DB,
    status: str = Form(...),
    feedback_notes: str = Form(default=""),
) -> Response:
    if status not in _VALID_FEEDBACK_STATUSES:
        # Antes esto devolvía `suggestions/index.html` entera — un documento con
        # `<html><head>` — y la plantilla apunta el swap a `#suggestion-{id}`, o sea
        # una tarjeta: el documento completo terminaba anidado adentro del `<body>`
        # abierto. `status` sale de un hidden propio, así que un valor inválido es una
        # request mal formada y le corresponde un 4xx, no una página.
        return _invalid(request, _("Invalid feedback status: %(status)s.", status=status))

    svc = SuggestionService(db)
    suggestion = svc.respond_to_suggestion(
        suggestion_id,
        SuggestionFeedback(status=status, feedback_notes=feedback_notes or None),
        current_user.id,
        current_user.household_id,
    )
    # Un id que no existe —o que es de otra persona— devolvía igual "listo, gracias":
    # el resultado del servicio se descartaba. Ver `SuggestionRepository.get_owned`.
    #
    # El mismo mensaje para los dos casos, a propósito: la respuesta no tiene que dejar
    # distinguir un id inexistente de uno ajeno. La ruta `/api/v1` contesta el 404 en
    # JSON, que es lo que le corresponde; acá la respuesta es SSR.
    if suggestion is None:
        return _invalid(
            request, _("That suggestion is no longer available."), status_code=404
        )
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "suggestions/partials/dismissed.html",
            {"request": request, "suggestion_id": suggestion_id},
        )
    return _back_to_suggestions(_("Response saved."), "success")


@router.post("/preferences")
def save_preference(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    item_type: str = Form(...),
    item_name: str = Form(...),
    preference_signal: str = Form(...),
) -> Response:
    # Misma clase de request mal formada que un `status` inválido, así que mismo
    # contrato: 4xx con HTMX, redirect con flash sin JS. Antes esta rama contestaba 200
    # con el fragmento de error adentro, o sea "salió bien" con un error en el cuerpo.
    if preference_signal not in _VALID_PREFERENCE_SIGNALS:
        return _invalid(
            request,
            _("Invalid signal: %(signal)s.", signal=preference_signal),
            back="/profile/",
        )

    item_name = item_name.strip()[:200]
    item_type = item_type.strip()[:40]

    svc = SuggestionService(db)
    svc.save_preference(
        current_user.id,
        RecommendationPreferenceCreate(
            item_type=item_type,
            item_name=item_name,
            preference_signal=preference_signal,
        ),
    )
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/flash_messages.html",
            {"request": request, "messages": [{"type": "success", "text": _("Preference saved.")}]},
        )
    return _back_to_profile(_("Preference saved."), "success")
