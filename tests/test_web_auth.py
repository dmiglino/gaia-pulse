"""Page tests for /login."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services.auth_service import _hash_token


def test_login_page_renders(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200, r.text[:500]
    assert 'name="email"' in r.text
    assert 'name="password"' in r.text


def test_login_page_wires_remember_and_forgot_password_for_real(client: TestClient) -> None:
    """Fase 7.10: ya no son promesas muertas — el checkbox postea y el link funciona."""
    r = client.get("/login")
    assert 'href="#"' not in r.text
    assert 'name="remember"' in r.text
    assert 'href="/forgot-password"' in r.text


def test_login_page_defines_the_brand_palette_it_uses(client: TestClient) -> None:
    """The page uses `brand-*` classes, so whatever config it gets must define them.

    Originally this page carried its own `tailwind.config` **without** the brand
    scale, so its brand colors never applied. The fix in v3 is structural: it
    extends `layouts/shell.html`, which is the single config. The assertion moved
    with it — the invariant is still "the classes this page uses are defined",
    only the palette now comes from the CSS tokens instead of an inline hex.
    """
    r = client.get("/login")
    assert "brand-500" in r.text
    assert "rgb(var(--brand-500)" in r.text
    assert "/static/css/app.css" in r.text


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


def test_login_without_remember_is_a_session_cookie(client: TestClient, diego: User) -> None:
    r = client.post(
        "/login",
        data={"email": diego.email, "password": "diego123"},
        follow_redirects=False,
    )
    cookie = next(c for c in r.cookies.jar if c.name == "gaiapulse_session")
    assert cookie.expires is None


def test_login_with_remember_is_a_persistent_cookie(client: TestClient, diego: User) -> None:
    r = client.post(
        "/login",
        data={"email": diego.email, "password": "diego123", "remember": "true"},
        follow_redirects=False,
    )
    cookie = next(c for c in r.cookies.jar if c.name == "gaiapulse_session")
    assert cookie.expires is not None


def test_forgot_password_page_renders(client: TestClient) -> None:
    r = client.get("/forgot-password")
    assert r.status_code == 200
    assert 'name="email"' in r.text


def test_forgot_password_same_response_for_unknown_email(client: TestClient) -> None:
    """Anti-enumeration: a nonexistent email must look exactly like a real one."""
    r = client.post("/forgot-password", data={"email": "nobody@test.com"})
    assert r.status_code == 200
    assert "restablecer tu contraseña" in r.text.lower()


def test_forgot_password_creates_token_and_sends_mail_for_real_email(
    client: TestClient, diego: User, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = {}

    def fake_send_email(to: str, subject: str, body: str) -> None:
        sent["to"] = to
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr("app.services.auth_service.send_email", fake_send_email)

    r = client.post("/forgot-password", data={"email": diego.email})
    assert r.status_code == 200

    token = db.query(PasswordResetToken).filter_by(user_id=diego.id).one()
    assert token.used_at is None

    assert sent["to"] == diego.email
    assert "reset-password/" in sent["body"]


def test_forgot_password_unknown_email_creates_no_token(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.auth_service.send_email", lambda *a, **k: pytest.fail("should not send")
    )
    client.post("/forgot-password", data={"email": "nobody@test.com"})
    assert db.query(PasswordResetToken).count() == 0


def test_reset_password_page_valid_token(client: TestClient, diego: User, db: Session) -> None:
    raw_token = "a-valid-raw-token"
    db.add(
        PasswordResetToken(
            user_id=diego.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()

    r = client.get(f"/reset-password/{raw_token}")
    assert r.status_code == 200
    assert "invalid" not in r.text.lower() or 'name="password"' in r.text
    assert 'name="password"' in r.text


def test_reset_password_page_expired_token_shows_invalid(
    client: TestClient, diego: User, db: Session
) -> None:
    raw_token = "an-expired-raw-token"
    db.add(
        PasswordResetToken(
            user_id=diego.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    db.commit()

    r = client.get(f"/reset-password/{raw_token}")
    assert r.status_code == 200
    assert 'name="password"' not in r.text


def test_reset_password_page_unknown_token_shows_invalid(client: TestClient) -> None:
    r = client.get("/reset-password/does-not-exist")
    assert r.status_code == 200
    assert 'name="password"' not in r.text


def test_reset_password_submit_updates_password(
    client: TestClient, diego: User, db: Session
) -> None:
    raw_token = "reset-me-now"
    db.add(
        PasswordResetToken(
            user_id=diego.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()

    r = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "newpassword123", "password_confirm": "newpassword123"},
        follow_redirects=False,
    )
    assert r.status_code == 200

    db.refresh(diego)
    assert verify_password("newpassword123", diego.password_hash)

    token = db.query(PasswordResetToken).filter_by(user_id=diego.id).one()
    assert token.used_at is not None


def test_reset_password_submit_rejects_mismatched_confirmation(
    client: TestClient, diego: User, db: Session
) -> None:
    raw_token = "mismatch-token"
    db.add(
        PasswordResetToken(
            user_id=diego.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()

    r = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "newpassword123", "password_confirm": "somethingelse"},
    )
    assert r.status_code == 200
    assert "match" in r.text.lower() or "coincid" in r.text.lower()

    db.refresh(diego)
    assert verify_password("diego123", diego.password_hash)


def test_reset_password_token_cannot_be_reused(
    client: TestClient, diego: User, db: Session
) -> None:
    raw_token = "single-use-token"
    db.add(
        PasswordResetToken(
            user_id=diego.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()

    first = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "firstpassword1", "password_confirm": "firstpassword1"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "secondpassword2", "password_confirm": "secondpassword2"},
    )
    assert second.status_code == 200
    assert 'name="password"' not in second.text

    db.refresh(diego)
    assert verify_password("firstpassword1", diego.password_hash)
