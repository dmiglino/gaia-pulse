from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.services.notification_service import NotificationService
from app.web.actions import notification_action
from app.web.helpers import get_template_context, templates

router = APIRouter()

# Los dos lugares donde `base.html` incluye el badge. Ver `notifications_badge`.
_BADGE_IDS = frozenset({"notif-badge", "notif-badge-mobile"})


@router.get("/", response_class=HTMLResponse)
def notifications_index(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    category: str | None = Query(None),
) -> HTMLResponse:
    svc = NotificationService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["notifications"] = svc.get_for_user(
        current_user.id,
        current_user.household_id,
        include_dismissed=False,
        limit=50,
        category=category,
    )
    ctx["unread_count"] = svc.get_unread_count(current_user.id, current_user.household_id)
    ctx["category_counts"] = svc.get_category_counts(current_user.id, current_user.household_id)
    ctx["filter_category"] = category
    return templates.TemplateResponse("notifications/index.html", ctx)


@router.get("/badge", response_class=HTMLResponse)
def notifications_badge(
    request: Request, current_user: CurrentUser, db: DB, badge_id: str = "notif-badge"
) -> HTMLResponse:
    """Return just the unread badge, so the nav can refresh it without a reload.

    The badge is rendered twice — desktop sidebar and mobile bottom bar — and each
    copy polls itself, so it has to come back carrying the same ``id`` it went out
    with or the second swap would produce a duplicate ``id``. Whitelisted rather
    than echoed: the value lands in an ``id`` attribute.
    """
    if badge_id not in _BADGE_IDS:
        badge_id = "notif-badge"

    svc = NotificationService(db)
    return templates.TemplateResponse(
        "notifications/partials/badge.html",
        {
            "request": request,
            "badge_id": badge_id,
            "unread_notifications_count": svc.get_unread_count(
                current_user.id, current_user.household_id
            ),
        },
    )


@router.post("/{notification_id}/read", response_class=HTMLResponse)
def mark_read(
    notification_id: int, request: Request, current_user: CurrentUser, db: DB
) -> Response:
    """Mark one notification read and hand back its re-rendered card.

    The template used to `hx-post` straight at `/api/v1/...`, which answers JSON:
    HTMX swapped that JSON into the page.
    """
    svc = NotificationService(db)
    notification = svc.mark_read(notification_id, current_user.id, current_user.household_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if not request.headers.get("HX-Request"):
        return RedirectResponse(url="/notifications/", status_code=302)
    return templates.TemplateResponse(
        "notifications/partials/notification_card.html",
        {"request": request, "n": notification},
    )


@router.post("/{notification_id}/act")
def act(notification_id: int, current_user: CurrentUser, db: DB) -> Response:
    """Ir a hacer lo que el aviso pide, y darlo por leído en el camino.

    Es el botón que la 4.3 le agrega a la tarjeta. Que además marque leído no es un
    extra: sin eso, actuar sobre un aviso lo dejaba contado en el globito del nav, o sea
    que la app seguía pidiendo lo que la persona acababa de hacer. Se marca leído y no
    descartado a propósito — descartar es de la persona, y el aviso sigue siendo el
    registro de que esto pasó hasta que ella lo saque o lo pode el job.

    No tiene rama `HX-Request`: es una navegación a otra pantalla, y la plantilla lo
    manda con un `<form>` sin `hx-*`. El destino sale del **mismo** `notification_action`
    que dibujó el botón (ver `app/web/actions.py`), así que el redirect no puede
    discrepar de lo que decía la etiqueta.
    """
    svc = NotificationService(db)
    notification = svc.mark_read(notification_id, current_user.id, current_user.household_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    action = notification_action(
        notification.category,
        notification.related_entity_type,
        notification.related_entity_id,
    )
    #: `or` y no un `if`: si a la categoría no le corresponde destino la plantilla no
    #: dibuja el botón, así que llegar acá sin acción es una URL escrita a mano. Marcar
    #: leído sigue siendo lo correcto para ese caso, y la lista es a dónde volver.
    return RedirectResponse(url=action.href if action else "/notifications/", status_code=302)


@router.post("/{notification_id}/dismiss", response_class=HTMLResponse)
def dismiss(notification_id: int, request: Request, current_user: CurrentUser, db: DB) -> Response:
    """Dismiss one notification; the card is replaced by nothing."""
    svc = NotificationService(db)
    if not svc.dismiss(notification_id, current_user.id, current_user.household_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse(url="/notifications/", status_code=302)


@router.post("/mark-all-read")
def mark_all_read(request: Request, current_user: CurrentUser, db: DB) -> Response:
    """Marcar todo como leído y volver a la lista.

    Antes esto tenía una rama `HX-Request` que devolvía `notifications/index.html`
    entera — una plantilla que extiende `base.html`, o sea un documento con
    `<html><head>`. La plantilla la llamaba con `hx-target="body"
    hx-swap="outerHTML"`, así que ese documento completo se insertaba adentro del
    `<body>` de la página que ya estaba abierta: `<html>` anidado, dos `<head>`, y
    el navegador reparando el árbol como pudiera. Nunca fue un swap válido.

    Marcar todo como leído cambia el estado de todas las tarjetas, del contador y de
    las pastillas de filtro: es una recarga, no un fragmento. Un POST y un 302 hacen
    exactamente eso, y además funciona sin JS.
    """
    svc = NotificationService(db)
    svc.mark_all_read(current_user.id, current_user.household_id)
    return RedirectResponse(url="/notifications/", status_code=302)
