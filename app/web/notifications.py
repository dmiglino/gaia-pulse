from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.dependencies import DB, CurrentUser
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


@router.post("/mark-all-read")
def mark_all_read(
    request: Request, current_user: CurrentUser, db: DB
) -> HTMLResponse:
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
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/notifications", status_code=302)
