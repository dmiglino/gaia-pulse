"""Web-layer tests for the onboarding wizard and the gate that forces it."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.body_metric import BodyMetricLog
from app.models.user import User


def _needs_onboarding(db: Session, user: User) -> None:
    user.onboarding_completed = False
    db.flush()


def test_gate_redirects_a_new_user_to_the_wizard(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    _needs_onboarding(db, diego)

    r = authenticated_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/onboarding/"


def test_gate_answers_htmx_with_a_client_side_redirect(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    _needs_onboarding(db, diego)

    r = authenticated_client.get("/pantry/", headers={"HX-Request": "true"})
    assert r.status_code == 204
    assert r.headers["HX-Redirect"] == "/onboarding/"


def test_wizard_renders_for_a_new_user(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    _needs_onboarding(db, diego)

    r = authenticated_client.get("/onboarding/")
    assert r.status_code == 200, r.text
    # Every field the POST handler reads must be present in the form.
    for field in (
        "birth_year",
        "sex",
        "height_cm",
        "weight_kg",
        "target_weight_kg",
        "baseline_activity_level",
        "dietary_restrictions",
    ):
        assert f'name="{field}"' in r.text, field
    assert 'action="/onboarding/complete"' in r.text
    # The wizard owns the viewport: no app navigation.
    assert 'href="/dashboard"' not in r.text


def test_completing_the_wizard_saves_the_profile_and_lifts_the_gate(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    _needs_onboarding(db, diego)

    r = authenticated_client.post(
        "/onboarding/complete",
        data={
            "birth_year": "1990",
            "sex": "male",
            "height_cm": "178",
            "weight_kg": "80.5",
            "target_weight_kg": "75",
            "baseline_activity_level": "active",
            "dietary_restrictions": "gluten, peanut",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"

    db.refresh(diego)
    assert diego.onboarding_completed is True
    assert diego.birth_date is not None and diego.birth_date.year == 1990
    assert diego.sex == "male"
    assert float(diego.height_cm) == 178.0
    assert float(diego.target_weight_kg) == 75.0
    assert diego.baseline_activity_level == "active"
    assert diego.dietary_restrictions_json == ["gluten", "peanut"]

    # The starting weight becomes the first body metric entry.
    logs = db.query(BodyMetricLog).filter(BodyMetricLog.user_id == diego.id).all()
    assert [float(log.weight_kg) for log in logs] == [80.5]

    # And the app is reachable again.
    r = authenticated_client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_skipping_only_marks_onboarding_done(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    _needs_onboarding(db, diego)

    r = authenticated_client.post("/onboarding/complete", follow_redirects=False)
    assert r.status_code == 302

    db.refresh(diego)
    assert diego.onboarding_completed is True
    assert diego.sex is None
    assert diego.dietary_restrictions_json is None


def test_wizard_redirects_home_once_completed(authenticated_client: TestClient) -> None:
    r = authenticated_client.get("/onboarding/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
