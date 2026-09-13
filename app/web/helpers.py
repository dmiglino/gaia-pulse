"""Shared helpers for web route handlers: template rendering with auth context."""
import hashlib

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.i18n import setup_jinja2_i18n
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.services.notification_service import NotificationService
from app.web.flash import read_flashes

_settings = get_settings()


class CompatJinja2Templates(Jinja2Templates):
    """Compatibility wrapper for Starlette TemplateResponse signature changes."""

    def TemplateResponse(self, *args, **kwargs):  # type: ignore[override]
        # New signature (Starlette >=1.0): TemplateResponse(request, name, context, ...)
        if args and isinstance(args[0], Request):
            return super().TemplateResponse(*args, **kwargs)

        # Backward compatibility for old call style:
        # TemplateResponse(name, context, status_code=...)
        if not args:
            return super().TemplateResponse(*args, **kwargs)

        name = args[0]
        context = args[1] if len(args) > 1 else kwargs.get("context")
        if context is None:
            context = {}
        request = context.get("request")
        if request is None:
            raise ValueError("Template context must include 'request'")

        remaining_args = args[2:]
        return super().TemplateResponse(request, name, context, *remaining_args, **kwargs)


templates = CompatJinja2Templates(directory="app/templates")
templates.env.globals["csrf_token"] = lambda request: hashlib.sha256(
    f"{request.url.path}:{request.client.host if request.client else 'local'}".encode()
).hexdigest()
templates.env.globals["locale"] = _settings.default_locale
setup_jinja2_i18n(templates.env, _settings.default_locale)


def get_template_context(request: Request, db: Session, current_user: User) -> dict:
    """Build the base template context with household and user data.

    ``base.html`` renders the notification badge and the flash messages on every
    page, so both belong here rather than in each individual route.
    """
    users = UserRepository(db).get_household_users(current_user.household_id)
    unread = NotificationService(db).get_unread_count(
        current_user.id, current_user.household_id
    )
    return {
        "request": request,
        "current_user": current_user,
        "users": users,
        "unread_notifications_count": unread,
        "flashes": read_flashes(request),
    }


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)
