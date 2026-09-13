"""One-shot flash messages for the server-rendered pages.

``get_flashed_messages()`` is a Flask API and does not exist here, so the base
template's flash block never rendered anything. This module replaces it: a
route that redirects stashes a message with :func:`set_flash`, the next rendered
page picks it up through ``get_template_context`` and the middleware in
``app.main`` clears it so it is shown exactly once.

The payload travels in a short-lived signed cookie because there is no session
middleware in this app — signing keeps the rendered text out of a user's own
control, and the 60s lifetime keeps a stale message from resurfacing later.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

_settings = get_settings()
_serializer = URLSafeTimedSerializer(_settings.app_secret_key, salt="flash")

FLASH_COOKIE_NAME = "gaiapulse_flash"
FLASH_MAX_AGE_SECONDS = 60

_VALID_CATEGORIES = frozenset({"success", "error", "warning", "info"})


def set_flash(response: Response, message: str, category: str = "info") -> None:
    """Queue *message* to be shown on the next page this browser renders."""
    if category not in _VALID_CATEGORIES:
        category = "info"
    response.set_cookie(
        FLASH_COOKIE_NAME,
        _serializer.dumps({"category": category, "message": message}),
        max_age=FLASH_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def read_flashes(request: Request) -> list[dict[str, str]]:
    """Return the pending flash messages for *request* (at most one today)."""
    raw = request.cookies.get(FLASH_COOKIE_NAME)
    if not raw:
        return []
    try:
        payload = _serializer.loads(raw, max_age=FLASH_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return []
    if not isinstance(payload, dict) or not payload.get("message"):
        return []
    category = str(payload.get("category", "info"))
    return [
        {
            "category": category if category in _VALID_CATEGORIES else "info",
            "message": str(payload["message"]),
        }
    ]


def clear_flash(response: Response) -> None:
    """Drop the flash cookie once its message has been rendered."""
    response.delete_cookie(FLASH_COOKIE_NAME, path="/")
