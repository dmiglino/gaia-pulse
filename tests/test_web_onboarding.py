"""Web-layer tests for the onboarding wizard and the gate that forces it."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.i18n import _
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


def test_a_number_it_cannot_read_is_named_instead_of_dropped(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """Las cuatro respuestas numéricas se descartaban en silencio.

    Cada una tenía su `except ValueError: pass` y su rango sin rama `else`, así que
    escribir "1,70" en la altura terminaba igual que no escribir nada: el wizard
    decía que estaba todo listo y el dato no existía. A diferencia del perfil acá no
    se descarta todo el envío — el wizard se ve una sola vez y levanta el gate, y
    negarse a terminarlo por un decimal mal escrito deja a la persona trabada —, así
    que se guarda lo que se entiende y se nombra lo que no.
    """
    _needs_onboarding(db, diego)

    resp = authenticated_client.post(
        "/onboarding/complete",
        data={
            "birth_year": "1990",
            "height_cm": "1,70",  # coma decimal: `float()` no lo lee
            "weight_kg": "8000",  # fuera de rango
            "target_weight_kg": "75",
            "baseline_activity_level": "active",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    landing = authenticated_client.get("/", follow_redirects=False)
    assert landing.status_code == 200
    #: El mensaje entero, no un `in` sobre "Peso": los dos campos con el mismo rótulo
    #: que el paso 2 les puso, en el orden en que se leen, y dónde arreglarlos. Sin
    #: esto la persona no tiene forma de saber qué se perdió.
    assert (
        _(
            "All set! I could not read these, so I left them out: %(fields)s."
            " You can add them from your profile.",
            fields=", ".join([_("Height"), _("Weight")]),
        )
        in landing.text
    )
    assert _("All set. Welcome to GaiaPulse.") not in landing.text

    db.refresh(diego)
    #: Lo que no se pudo leer no se inventó...
    assert diego.height_cm is None
    assert db.query(BodyMetricLog).filter(BodyMetricLog.user_id == diego.id).all() == []
    #: ...y lo que sí, se guardó: el envío no se descarta entero.
    assert diego.birth_date is not None and diego.birth_date.year == 1990
    assert float(diego.target_weight_kg) == 75.0
    assert diego.baseline_activity_level == "active"
    #: Y el gate queda levantado igual: el wizard no atrapa a nadie.
    assert diego.onboarding_completed is True


def test_a_year_of_birth_with_decimals_is_not_silently_truncated(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """El año se parsea con `int()` exacto, no con un `float()` redondeado.

    Es el mismo campo que los otros tres pero no comparte su ayudante: leer "1990.5"
    como 1990 sería adivinar una fecha de nacimiento, y una fecha de nacimiento
    inventada alimenta después el cálculo de gasto energético.
    """
    _needs_onboarding(db, diego)

    resp = authenticated_client.post(
        "/onboarding/complete",
        data={"birth_year": "19.5"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db.refresh(diego)
    assert diego.birth_date is None

    landing = authenticated_client.get("/", follow_redirects=False)
    assert (
        _(
            "All set! I could not read these, so I left them out: %(fields)s."
            " You can add them from your profile.",
            fields=_("Year of birth"),
        )
        in landing.text
    )


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
