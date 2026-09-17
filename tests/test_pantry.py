"""Tests for pantry stock management."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.food import FoodItem
from app.models.household import Household
from app.models.user import User
from app.schemas.pantry import PurchaseItem, PurchaseRequest, StockAdjustRequest
from app.services.pantry_service import PantryService


def make_food(db: Session, name: str) -> FoodItem:
    f = FoodItem(canonical_name=name, category="other", base_unit="unit", perishable=False)
    db.add(f)
    db.flush()
    return f


class TestPantryPurchase:
    def test_purchase_creates_stock(self, db: Session, household: Household, diego: User) -> None:
        make_food(db, "apple")
        svc = PantryService(db)
        req = PurchaseRequest(items=[PurchaseItem(food_name="apple", quantity=5, unit="unit")])
        svc.process_purchase(household.id, diego.id, req)

        stock = svc.get_stock(household.id)
        apple_stock = next((s for s in stock if s.food_item.canonical_name == "apple"), None)
        assert apple_stock is not None
        assert float(apple_stock.current_quantity) == 5

    def test_purchase_multiple_items(self, db: Session, household: Household, diego: User) -> None:
        make_food(db, "bread")
        make_food(db, "milk")
        svc = PantryService(db)
        req = PurchaseRequest(
            items=[
                PurchaseItem(food_name="bread", quantity=2, unit="unit"),
                PurchaseItem(food_name="milk", quantity=1000, unit="ml"),
            ]
        )
        movements = svc.process_purchase(household.id, diego.id, req)
        assert len(movements) == 2

    def test_purchase_adds_to_existing_stock(
        self, db: Session, household: Household, diego: User
    ) -> None:
        make_food(db, "orange")
        svc = PantryService(db)
        req1 = PurchaseRequest(items=[PurchaseItem(food_name="orange", quantity=3, unit="unit")])
        req2 = PurchaseRequest(items=[PurchaseItem(food_name="orange", quantity=2, unit="unit")])
        svc.process_purchase(household.id, diego.id, req1)
        svc.process_purchase(household.id, diego.id, req2)

        stock = svc.get_stock(household.id)
        orange_stock = next(s for s in stock if s.food_item.canonical_name == "orange")
        assert float(orange_stock.current_quantity) == 5


class TestStockAdjustment:
    def test_consumption_reduces_stock(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        svc = PantryService(db)
        # First add stock
        req = PurchaseRequest(items=[PurchaseItem(food_name="banana", quantity=6, unit="unit")])
        svc.process_purchase(household.id, diego.id, req)

        # Then consume
        svc.adjust_stock(
            household.id,
            diego.id,
            StockAdjustRequest(
                food_name="banana", quantity=2, unit="unit", movement_type="consumption"
            ),
        )
        stock = svc.get_stock(household.id)
        b = next(s for s in stock if s.food_item.canonical_name == "banana")
        assert float(b.current_quantity) == 4

    def test_stock_never_goes_negative(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        svc = PantryService(db)
        # Add 2
        req = PurchaseRequest(items=[PurchaseItem(food_name="banana", quantity=2, unit="unit")])
        svc.process_purchase(household.id, diego.id, req)
        # Consume 10 — should clamp to 0
        svc.adjust_stock(
            household.id,
            diego.id,
            StockAdjustRequest(
                food_name="banana", quantity=10, unit="unit", movement_type="consumption"
            ),
        )
        stock = svc.get_stock(household.id)
        b = next(s for s in stock if s.food_item.canonical_name == "banana")
        assert float(b.current_quantity) == 0


class TestLedgerMatchesStock:
    """The movement ledger must record what moved, not what was asked for.

    Stock is clamped at zero, so an over-draw used to be written down at its
    requested magnitude: `/pantry/movements` claimed a consumption of 10 units
    of an item that never held more than 2, and stock could no longer be
    reconciled from its own movements.
    """

    def test_overdraw_records_only_what_left_the_pantry(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        svc = PantryService(db)
        svc.process_purchase(
            household.id,
            diego.id,
            PurchaseRequest(items=[PurchaseItem(food_name="banana", quantity=2, unit="unit")]),
        )
        svc.adjust_stock(
            household.id,
            diego.id,
            StockAdjustRequest(
                food_name="banana", quantity=10, unit="unit", movement_type="consumption"
            ),
        )
        consumed = [
            float(m.quantity)
            for m in svc.get_movements(household.id)
            if m.movement_type == "consumption"
        ]
        assert consumed == [2.0]

    def test_overdraw_by_stock_id_records_only_what_left(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        svc = PantryService(db)
        svc.process_purchase(
            household.id,
            diego.id,
            PurchaseRequest(items=[PurchaseItem(food_name="banana", quantity=2, unit="unit")]),
        )
        stock_row = next(
            s for s in svc.get_stock(household.id) if s.food_item.canonical_name == "banana"
        )
        svc.adjust_stock_by_id(
            household.id, diego.id, stock_row.id, quantity=5, movement_type="consumption"
        )
        consumed = [
            float(m.quantity)
            for m in svc.get_movements(household.id)
            if m.movement_type == "consumption"
        ]
        assert consumed == [2.0]
        assert float(stock_row.current_quantity) == 0

    def test_stock_equals_the_sum_of_its_movements(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        svc = PantryService(db)
        svc.process_purchase(
            household.id,
            diego.id,
            PurchaseRequest(items=[PurchaseItem(food_name="banana", quantity=6, unit="unit")]),
        )
        for qty in (2, 3, 4):  # the last one over-draws
            svc.adjust_stock(
                household.id,
                diego.id,
                StockAdjustRequest(
                    food_name="banana", quantity=qty, unit="unit", movement_type="consumption"
                ),
            )
        signs = {"purchase": 1.0, "consumption": -1.0, "discard": -1.0}
        ledger = sum(
            signs[m.movement_type] * float(m.quantity) for m in svc.get_movements(household.id)
        )
        b = next(s for s in svc.get_stock(household.id) if s.food_item.canonical_name == "banana")
        assert ledger == float(b.current_quantity) == 0


class TestLowStock:
    def test_low_stock_detection(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        from app.models.pantry import PantryStock

        stock = PantryStock(
            household_id=household.id,
            food_item_id=banana.id,
            current_quantity=1,
            unit="unit",
            low_stock_threshold=3,
        )
        db.add(stock)
        db.flush()

        svc = PantryService(db)
        low = svc.get_low_stock(household.id)
        assert len(low) >= 1
        assert any(s.food_item.canonical_name == "banana" for s in low)

    def test_above_threshold_not_low(
        self, db: Session, household: Household, diego: User, banana: FoodItem
    ) -> None:
        from app.models.pantry import PantryStock

        stock = PantryStock(
            household_id=household.id,
            food_item_id=banana.id,
            current_quantity=10,
            unit="unit",
            low_stock_threshold=3,
        )
        db.add(stock)
        db.flush()

        svc = PantryService(db)
        low = svc.get_low_stock(household.id)
        assert not any(s.food_item.canonical_name == "banana" for s in low)


class TestShoppingList:
    def test_shopping_page_shows_low_and_out_of_stock(
        self, authenticated_client: TestClient, db: Session, household: Household, diego: User
    ) -> None:
        from app.models.pantry import PantryStock

        food = make_food(db, "olive_oil")
        stock = PantryStock(
            household_id=household.id,
            food_item_id=food.id,
            current_quantity=0,
            unit="unit",
            low_stock_threshold=2,
        )
        db.add(stock)
        db.flush()

        resp = authenticated_client.get("/pantry/shopping")
        assert resp.status_code == 200
        assert "shopping-list" in resp.text
        assert "Olive_oil" in resp.text

    def test_buy_item_restores_stock(
        self, authenticated_client: TestClient, db: Session, household: Household, diego: User
    ) -> None:
        from app.models.pantry import PantryStock

        food = make_food(db, "eggs")
        stock = PantryStock(
            household_id=household.id,
            food_item_id=food.id,
            current_quantity=0,
            unit="unit",
            low_stock_threshold=6,
        )
        db.add(stock)
        db.flush()

        resp = authenticated_client.post(f"/pantry/shopping/{stock.id}/buy")
        assert resp.status_code == 200

        db.refresh(stock)
        assert float(stock.current_quantity) >= 6
