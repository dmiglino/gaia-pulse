"""Tests for pantry stock management."""
import pytest
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
    def test_purchase_creates_stock(
        self, db: Session, household: Household, diego: User
    ) -> None:
        make_food(db, "apple")
        svc = PantryService(db)
        req = PurchaseRequest(items=[PurchaseItem(food_name="apple", quantity=5, unit="unit")])
        svc.process_purchase(household.id, diego.id, req)

        stock = svc.get_stock(household.id)
        apple_stock = next((s for s in stock if s.food_item.canonical_name == "apple"), None)
        assert apple_stock is not None
        assert float(apple_stock.current_quantity) == 5

    def test_purchase_multiple_items(
        self, db: Session, household: Household, diego: User
    ) -> None:
        make_food(db, "bread")
        make_food(db, "milk")
        svc = PantryService(db)
        req = PurchaseRequest(items=[
            PurchaseItem(food_name="bread", quantity=2, unit="unit"),
            PurchaseItem(food_name="milk", quantity=1000, unit="ml"),
        ])
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
            household.id, diego.id,
            StockAdjustRequest(food_name="banana", quantity=2, unit="unit", movement_type="consumption")
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
            household.id, diego.id,
            StockAdjustRequest(food_name="banana", quantity=10, unit="unit", movement_type="consumption")
        )
        stock = svc.get_stock(household.id)
        b = next(s for s in stock if s.food_item.canonical_name == "banana")
        assert float(b.current_quantity) == 0


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
