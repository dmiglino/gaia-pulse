from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.repositories.notification_repo import NotificationRepository
from app.services.notification_service import NotificationService
from app.web.helpers import get_template_context, templates

router = APIRouter()


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
    ctx["filter_category"] = category
    return templates.TemplateResponse("notifications/index.html", ctx)


@router.get("/badge", response_class=HTMLResponse)
def notifications_badge(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    """Return just the unread badge, so the nav can refresh it without a reload."""
    svc = NotificationService(db)
    return templates.TemplateResponse(
        "notifications/partials/badge.html",
        {
            "request": request,
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
    if not svc.mark_read(notification_id, current_user.id, current_user.household_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return _card_or_redirect(request, db, current_user, notification_id)


@router.post("/{notification_id}/dismiss", response_class=HTMLResponse)
def dismiss(
    notification_id: int, request: Request, current_user: CurrentUser, db: DB
) -> Response:
    """Dismiss one notification; the card is replaced by nothing."""
    svc = NotificationService(db)
    if not svc.dismiss(notification_id, current_user.id, current_user.household_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    if request.headers.get("HX-Request"):
        return HTMLResponse("")
    return RedirectResponse(url="/notifications/", status_code=302)


def _card_or_redirect(
    request: Request, db: DB, current_user: CurrentUser, notification_id: int
) -> Response:
    if not request.headers.get("HX-Request"):
        return RedirectResponse(url="/notifications/", status_code=302)
    notification = NotificationRepository(db).get(notification_id)
    return templates.TemplateResponse(
        "notifications/partials/notification_card.html",
        {"request": request, "n": notification},
    )


@router.post("/mark-all-read")
def mark_all_read(request: Request, current_user: CurrentUser, db: DB) -> Response:
    svc = NotificationService(db)
    svc.mark_all_read(current_user.id, current_user.household_id)
    if request.headers.get("HX-Request"):
        ctx = get_template_context(request, db, current_user)
        ctx["notifications"] = svc.get_for_user(
            current_user.id, current_user.household_id, limit=50
        )
        ctx["unread_count"] = 0
        ctx["filter_category"] = None
        return templates.TemplateResponse("notifications/index.html", ctx)
    return RedirectResponse(url="/notifications/", status_code=302)
