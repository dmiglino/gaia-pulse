"""Page tests for /login."""

from fastapi.testclient import TestClient

from app.models.user import User


def test_login_page_renders(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200, r.text[:500]
    assert 'name="email"' in r.text
    assert 'name="password"' in r.text


def test_login_page_drops_promises_the_app_does_not_keep(client: TestClient) -> None:
    """No dead "Forgot password?" link and no `remember` checkbox the backend ignores."""
    r = client.get("/login")
    assert 'href="#"' not in r.text
    assert 'name="remember"' not in r.text


def test_login_page_defines_the_brand_palette_it_uses(client: TestClient) -> None:
    """This page carries its own tailwind.config, so it needs its own `brand` scale."""
    r = client.get("/login")
    assert "brand-500" in r.text
    assert "#6366f1" in r.text


def test_failed_login_keeps_the_email(client: TestClient, diego: User) -> None:
    r = client.post("/login", data={"email": diego.email, "password": "wrong"})
    assert r.status_code == 401
    assert f'value="{diego.email}"' in r.text


def test_successful_login_redirects_home(client: TestClient, diego: User) -> None:
    r = client.post(
        "/login",
        data={"email": diego.email, "password": "diego123"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"
