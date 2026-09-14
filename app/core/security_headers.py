"""Security response headers, applied to every response.

GaiaPulse renders personal health data in a browser shared by two people, and
`app/main.py` already refuses to cache it (`privacy_headers`). That same
middleware also already sets `Referrer-Policy: same-origin` with reasoning
specific to this app (query strings can carry a `user_id`, and four CDNs are
loaded on every page) — stricter than the generic
`strict-origin-when-cross-origin` a from-scratch policy would reach for, so
`Referrer-Policy` stays owned there rather than being set a second time here
with a weaker value.

This module is the sibling concern: hardening the *browser's* handling of the
response against framing, MIME-sniffing and cross-origin leakage, regardless
of what the response contains.

The CSP is intentionally partial, not an oversight: the app has no frontend
build step (Tailwind Play CDN, HTMX, Alpine and Chart.js all load from a
CDN — see `app/templates/layouts/shell.html`), so `script-src`/`style-src`
must allowlist those exact hosts instead of `'self'` alone. `style-src`
additionally needs `'unsafe-inline'` because the Tailwind Play CDN script
generates utility classes at runtime by injecting a `<style>` tag — there is
no build step to pre-generate a stylesheet instead, so this is an accepted
gap tied to that constraint, not a default we forgot to tighten.
"""

from typing import Any

from fastapi import Request

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

STRICT_TRANSPORT_SECURITY = "max-age=63072000; includeSubDomains"


async def security_headers(request: Request, call_next: Any) -> Any:
    """Set the headers a browser needs to defend the page on its own.

    Registered the same way as the other cross-cutting middleware in
    `app/main.py` (`@app.middleware("http")`), so there is one pattern for
    "runs on every response" in this codebase, not two.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # Referrer-Policy is already set by `privacy_headers` in `app/main.py`
    # (`same-origin`, stricter than the default this module would otherwise
    # add) — see the module docstring above.
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    # No reverse proxy sits in front of the app (see docker-compose.yml — the
    # `app` service is the one exposing port 8000 directly), so there is no
    # `X-Forwarded-Proto` to trust. Gate on the request's own scheme.
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY)
    return response
