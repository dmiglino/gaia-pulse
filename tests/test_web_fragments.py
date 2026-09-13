"""Web-layer tests for the HTMX fragment routes added in v3."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.pantry import PantryStock
from app.models.user import User


def test_notifications_badge(authenticated_client: TestClient) -> None:
    r = authenticated_client.get("/notifications/badge")
    assert r.status_code == 200, r.text
    assert 'id="notif-badge"' in r.text


def test_suggestions_index_and_generate(authenticated_client: TestClient) -> None:
    r = authenticated_client.get("/suggestions/")
    assert r.status_code == 200, r.text
    assert 'id="suggestions-list"' in r.text

    r = authenticated_client.post("/suggestions/generate", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text
    assert 'id="suggestions-list"' in r.text


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


def test_home_and_pantry_pages_render(authenticated_client: TestClient) -> None:
    for path in ("/", "/pantry/"):
        r = authenticated_client.get(path)
        assert r.status_code == 200, (path, r.text[:500])
