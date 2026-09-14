"""Web-layer tests for the HTMX fragment routes added in v3."""

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.i18n import _
from app.models.food import FoodItem
from app.models.household import Household
from app.models.pantry import PantryStock
from app.models.user import User
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService
from app.web.flash import FLASH_COOKIE_NAME


def test_notifications_badge(authenticated_client: TestClient) -> None:
    r = authenticated_client.get("/notifications/badge")
    assert r.status_code == 200, r.text
    assert 'id="notif-badge"' in r.text


def test_notification_read_and_dismiss_return_fragments(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    """These buttons used to `hx-post` at `/api/v1/...`, which answers JSON —
    HTMX swapped that JSON straight into the page."""
    svc = NotificationService(db)
    n = svc.create(
        NotificationCreate(
            user_id=diego.id,
            household_id=diego.household_id,
            category="inactivity",
            title="No workouts in 4 days",
            body="Time to move!",
        )
    )

    r = authenticated_client.post(f"/notifications/{n.id}/read", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    assert f'id="notif-{n.id}"' in r.text
    assert "No workouts in 4 days" in r.text
    # The re-rendered card must have lost the unread styling.
    assert "bg-indigo-50/30" not in r.text
    db.refresh(n)
    assert n.is_read

    r = authenticated_client.post(f"/notifications/{n.id}/dismiss", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    assert r.text.strip() == ""
    db.refresh(n)
    assert n.is_dismissed


def test_cannot_act_on_another_households_notification(
    authenticated_client: TestClient, db: Session
) -> None:
    """The id in the URL must not be enough: scope by the acting user."""
    other_home = Household(name="Someone else", timezone="UTC")
    db.add(other_home)
    db.flush()
    n = NotificationService(db).create(
        NotificationCreate(
            household_id=other_home.id,
            category="low_stock",
            title="Their pantry",
            body="Not yours",
        )
    )

    for action in ("read", "dismiss"):
        r = authenticated_client.post(
            f"/notifications/{n.id}/{action}", headers={"HX-Request": "true"}
        )
        assert r.status_code == 404, (action, r.text)
    db.refresh(n)
    assert not n.is_read
    assert not n.is_dismissed


def test_suggestions_index_and_generate(authenticated_client: TestClient) -> None:
    r = authenticated_client.get("/suggestions/")
    assert r.status_code == 200, r.text
    assert 'id="suggestions-list"' in r.text

    r = authenticated_client.post("/suggestions/generate", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    assert 'id="suggestions-list"' in r.text
    #: El swap reemplaza `#suggestions-list`, o sea que la respuesta es un fragmento:
    #: un documento entero quedaría anidado adentro del `<body>` abierto.
    assert "<html" not in r.text


def test_pantry_adjust(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    stock = PantryStock(
        household_id=diego.household_id,
        food_item_id=banana.id,
        current_quantity=5.0,
        unit="unit",
    )
    db.add(stock)
    db.flush()

    r = authenticated_client.post(
        f"/pantry/{stock.id}/adjust",
        data={"quantity": "2", "movement_type": "adjustment"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text
    assert f'id="stock-item-{stock.id}"' in r.text
    # The card used to print `item.food_item.name`, an attribute `FoodItem` does
    # not have (it is `canonical_name`), so every card showed a blank name.
    assert banana.canonical_name in r.text
    db.refresh(stock)
    assert float(stock.current_quantity) == 2.0

    r = authenticated_client.post(
        f"/pantry/{stock.id}/adjust",
        data={"quantity": "3", "movement_type": "purchase"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text
    db.refresh(stock)
    assert float(stock.current_quantity) == 5.0

    r = authenticated_client.post(
        f"/pantry/{stock.id}/adjust",
        data={"quantity": "1", "movement_type": "bogus"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400

    r = authenticated_client.post(
        "/pantry/999999/adjust",
        data={"quantity": "1", "movement_type": "adjustment"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_pantry_set_threshold(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    stock = PantryStock(
        household_id=diego.household_id,
        food_item_id=banana.id,
        current_quantity=5.0,
        unit="unit",
    )
    db.add(stock)
    db.flush()

    r = authenticated_client.post(
        f"/pantry/{stock.id}/threshold",
        data={"low_stock_threshold": "3"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text
    assert f'id="stock-item-{stock.id}"' in r.text
    db.refresh(stock)
    assert float(stock.low_stock_threshold) == 3.0

    # Blank clears it back to "low means zero" instead of 422ing.
    r = authenticated_client.post(
        f"/pantry/{stock.id}/threshold",
        data={"low_stock_threshold": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text
    db.refresh(stock)
    assert stock.low_stock_threshold is None

    r = authenticated_client.post(
        f"/pantry/{stock.id}/threshold",
        data={"low_stock_threshold": "-1"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400

    r = authenticated_client.post(
        "/pantry/999999/threshold",
        data={"low_stock_threshold": "3"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_cannot_set_threshold_on_another_households_stock(
    authenticated_client: TestClient, db: Session
) -> None:
    """The id in the URL must not be enough: scope by the acting household."""
    other_home = Household(name="Someone else", timezone="UTC")
    db.add(other_home)
    db.flush()
    other_food = FoodItem(canonical_name="their food", category="other", base_unit="unit")
    db.add(other_food)
    db.flush()
    other_stock = PantryStock(
        household_id=other_home.id,
        food_item_id=other_food.id,
        current_quantity=5.0,
        unit="unit",
    )
    db.add(other_stock)
    db.flush()

    r = authenticated_client.post(
        f"/pantry/{other_stock.id}/threshold",
        data={"low_stock_threshold": "3"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404, r.text
    db.refresh(other_stock)
    assert other_stock.low_stock_threshold is None


def test_home_and_pantry_pages_render(authenticated_client: TestClient) -> None:
    for path in ("/", "/pantry/"):
        r = authenticated_client.get(path)
        assert r.status_code == 200, (path, r.text[:500])


def test_pages_that_loop_over_rows_render_their_names(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    """Render the `{% include %}`-inside-`{% for %}` paths with real rows.

    With an empty pantry and no notifications these loops never execute, so the
    partials extracted in v3 were exercised by nothing — which is how a card
    printing a non-existent attribute stayed invisible.
    """
    db.add(
        PantryStock(
            household_id=diego.household_id,
            food_item_id=banana.id,
            current_quantity=1.0,
            low_stock_threshold=5.0,
            unit="unit",
        )
    )
    NotificationService(db).create(
        NotificationCreate(
            user_id=diego.id,
            household_id=diego.household_id,
            category="low_stock",
            title="Running low",
            body="Buy more bananas",
        )
    )
    db.flush()

    r = authenticated_client.get("/pantry/")
    assert r.status_code == 200, r.text[:500]
    assert banana.canonical_name in r.text

    r = authenticated_client.get("/notifications/")
    assert r.status_code == 200, r.text[:500]
    assert "Running low" in r.text
    assert "Buy more bananas" in r.text

    # Home lists the low-stock names in its alert banner.
    r = authenticated_client.get("/")
    assert r.status_code == 200, r.text[:500]
    assert banana.canonical_name in r.text


def test_flash_survives_a_redirect_and_is_shown_once(
    authenticated_client: TestClient, db: Session, diego: User, banana: FoodItem
) -> None:
    stock = PantryStock(
        household_id=diego.household_id,
        food_item_id=banana.id,
        current_quantity=5.0,
        unit="unit",
    )
    db.add(stock)
    db.flush()

    # A non-HTMX POST redirects, stashing the outcome in the flash cookie.
    r = authenticated_client.post(
        f"/pantry/{stock.id}/adjust",
        data={"quantity": "2", "movement_type": "adjustment"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert FLASH_COOKIE_NAME in r.cookies

    # The next page renders it...
    r = authenticated_client.get("/pantry/")
    assert 'id="flash-messages"' in r.text
    assert _("Stock updated.") in r.text

    # ...and only once.
    r = authenticated_client.get("/pantry/")
    assert 'id="flash-messages"' not in r.text


def test_flash_messages_component_accepts_type_text_aliases(
    authenticated_client: TestClient,
) -> None:
    r = authenticated_client.post(
        "/suggestions/preferences",
        data={"item_type": "food", "item_name": "kale", "preference_signal": "likes"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200, r.text
    assert _("Preference saved.") in r.text


def test_no_template_htmx_call_targets_the_json_api() -> None:
    """The test that would have caught the whole routing defect class.

    `AGENTS.md` keeps the two routings apart: `/api/...` answers JSON, page
    routes answer HTML. An `hx-post` at a JSON route swaps JSON into the DOM.
    """
    from pathlib import Path

    offenders = []
    for tpl in Path("app/templates").rglob("*.html"):
        for lineno, line in enumerate(tpl.read_text().splitlines(), 1):
            if re.search(r'(hx-(get|post|put|delete)|action)="/api/', line):
                offenders.append(f"{tpl}:{lineno}")
    assert not offenders, offenders
