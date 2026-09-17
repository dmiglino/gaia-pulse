"""Coverage for `app/core/security_headers.py`.

Every response — API, page, static, error — goes through this middleware,
so these tests hit a couple of different response shapes rather than
re-testing the same route four times.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_login_page_gets_the_baseline_headers(client: TestClient) -> None:
    resp = client.get("/login")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in resp.headers


def test_json_api_401_also_gets_the_headers(client: TestClient) -> None:
    """The middleware runs on every response, not just page routes."""
    resp = client.get("/api/v1/meals")
    assert resp.status_code == 401
    assert resp.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in resp.headers


def test_csp_allowlists_exactly_the_cdns_the_shell_loads(client: TestClient) -> None:
    csp = client.get("/login").headers["content-security-policy"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    assert directives["default-src"] == "'self'"
    # Tailwind Play CDN, HTMX (unpkg) and Alpine/Chart.js (jsdelivr) — the
    # exact three hosts `layouts/shell.html` loads a <script src> from.
    # 'unsafe-inline' and 'unsafe-eval' are accepted gaps: inline scripts
    # configure Tailwind and initialize the theme, Alpine compiles directive
    # expressions via AsyncFunction/eval, and Tailwind's Play CDN compiles JIT
    # classes at runtime.
    assert directives["script-src"] == (
        "'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com "
        "https://cdn.jsdelivr.net"
    )
    # 'unsafe-inline' is the accepted gap: Tailwind's Play CDN injects a
    # <style> tag at runtime, and there is no build step to avoid that with.
    assert directives["style-src"] == ("'self' 'unsafe-inline' https://fonts.googleapis.com")
    assert directives["font-src"] == "'self' https://fonts.gstatic.com"
    assert directives["frame-ancestors"] == "'none'"


def test_hsts_is_absent_over_plain_http(client: TestClient) -> None:
    resp = client.get("/login")
    assert "strict-transport-security" not in resp.headers


def test_hsts_appears_once_the_request_is_https() -> None:
    https_client = TestClient(app, base_url="https://testserver")
    resp = https_client.get("/login")
    assert resp.headers["strict-transport-security"] == ("max-age=63072000; includeSubDomains")


def test_referrer_policy_stays_owned_by_privacy_headers(client: TestClient) -> None:
    """`security_headers` deliberately does not set this: `privacy_headers`

    in `app/main.py` already sets a stricter, app-specific value
    (`same-origin`), and the two must not race to set it differently.
    """
    resp = client.get("/login")
    assert resp.headers["referrer-policy"] == "same-origin"
