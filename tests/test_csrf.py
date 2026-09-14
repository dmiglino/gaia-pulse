"""Double-submit CSRF protection (`app/core/csrf.py`).

The `client` fixture auto-injects a matching cookie + form field for every
other test in the suite (see `tests/conftest.py`), so the negative paths here
call `TestClient.post`/`.get` unbound to bypass that convenience wrapper and
exercise the real check.
"""

from fastapi.testclient import TestClient

from app.core.csrf import CSRF_COOKIE_NAME
from app.models.user import User


def test_a_post_without_any_csrf_cookie_is_rejected(client: TestClient, diego: User) -> None:
    client.cookies.delete(CSRF_COOKIE_NAME)
    r = TestClient.post(client, "/login", data={"email": diego.email, "password": "diego123"})
    assert r.status_code == 403


def test_a_post_with_a_mismatched_token_is_rejected(client: TestClient, diego: User) -> None:
    r = TestClient.post(
        client,
        "/login",
        data={
            "email": diego.email,
            "password": "diego123",
            "csrf_token": "not-the-cookie-value",
        },
    )
    assert r.status_code == 403


def test_a_post_with_the_matching_cookie_and_field_succeeds(
    client: TestClient, diego: User
) -> None:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    r = TestClient.post(
        client,
        "/login",
        data={"email": diego.email, "password": "diego123", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 302


def test_a_get_request_renders_a_token_that_a_form_can_submit_back(
    client: TestClient,
) -> None:
    client.cookies.delete(CSRF_COOKIE_NAME)
    r = TestClient.get(client, "/login")
    assert r.status_code == 200
    cookie = r.cookies.get(CSRF_COOKIE_NAME)
    assert cookie
    assert f'value="{cookie}"' in r.text


def test_the_json_api_is_exempt_from_csrf_enforcement(client: TestClient, diego: User) -> None:
    client.cookies.delete(CSRF_COOKIE_NAME)
    r = TestClient.post(
        client,
        "/api/v1/auth/login",
        json={"email": diego.email, "password": "diego123"},
    )
    assert r.status_code != 403


def test_a_multipart_api_route_is_not_exempt_just_for_living_under_api(
    client: TestClient,
) -> None:
    """`/api/v1/nlp/transcribe` takes a plain `UploadFile` (multipart), which a
    forged cross-site form can send — unlike JSON, which no `<form>` can send.
    A path-based `/api/` exemption would have missed exactly this route.
    """
    client.cookies.delete(CSRF_COOKIE_NAME)
    r = TestClient.post(
        client,
        "/api/v1/nlp/transcribe",
        files={"audio": ("clip.webm", b"fake-audio", "audio/webm")},
    )
    assert r.status_code == 403


def test_a_rejected_post_still_carries_the_apps_security_headers(
    client: TestClient, diego: User
) -> None:
    """The 403 short-circuit must not skip `security_headers`/`privacy_headers` —
    this app renders personal health data in a browser shared by two people.
    """
    client.cookies.delete(CSRF_COOKIE_NAME)
    r = TestClient.post(client, "/login", data={"email": diego.email, "password": "diego123"})
    assert r.status_code == 403
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
