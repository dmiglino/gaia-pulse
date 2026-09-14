"""Double-submit CSRF protection for form-postable requests.

`app/web/helpers.py` already exposed a `csrf_token(request)` template global and
every mutating form in `app/templates/` already renders it into a hidden field —
but nothing ever checked it back. That gap is why this module exists.

The exemption is by *content type*, not by path: a plain cross-site `<form>`
can only submit `application/x-www-form-urlencoded` or `multipart/form-data`
(the browser restricts a form's `enctype` to those, plus `text/plain`) — never
`application/json`. So JSON requests are exempt regardless of path, and a form-
or multipart-bodied request is checked regardless of path. A path-based
`/api/` exemption would have been wrong: `POST /api/v1/nlp/transcribe` takes a
plain `UploadFile` (`multipart/form-data`), which a forged cross-site form can
send just as easily as a `<form>` under `/capture/`.

Design, and why it is the synchronizer pattern rather than a signed token tied
to the session cookie: the login form (`auth/login.html`) renders a CSRF field
before any session exists, so a token derived from the session would have
nothing to bind to there. A random value stored in its own cookie works for
both the logged-out and logged-in cases with the same code path — the browser
is the one holding the secret, not the session.
"""

import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings

settings = get_settings()

CSRF_COOKIE_NAME = "csrf_token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_FORM_CONTENT_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})


async def csrf_protection(request: Request, call_next: Any) -> Any:
    """Issue the double-submit cookie and validate it on unsafe form submissions.

    Every response carries the cookie (issuing a new one on first visit), and
    `request.state.csrf_token` is always set so `csrf_token(request)` in
    templates can render the same value into the form. An unsafe method
    (anything but GET/HEAD/OPTIONS) submitting a form or multipart body must
    submit that same value back as the `csrf_token` field, compared with
    `secrets.compare_digest` so a mismatch takes constant time to reject.
    """
    token = request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)
    request.state.csrf_token = token

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if request.method not in _SAFE_METHODS and content_type in _FORM_CONTENT_TYPES:
        # `BaseHTTPMiddleware` only replays the body to the route handler's own
        # `Form(...)` parsing if `request.body()` was called here — `.form()`
        # alone reads via `.stream()`, which the replay logic doesn't cache, so
        # without this the handler would see an empty body behind our back.
        await request.body()
        form = await request.form()
        submitted = form.get("csrf_token")
        if not isinstance(submitted, str) or not secrets.compare_digest(submitted, token):
            return PlainTextResponse("Invalid or missing CSRF token.", status_code=403)

    response = await call_next(request)
    if request.cookies.get(CSRF_COOKIE_NAME) != token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=token,
            max_age=settings.session_max_age_seconds,
            httponly=True,
            samesite="lax",
            secure=settings.is_production,
        )
    return response
