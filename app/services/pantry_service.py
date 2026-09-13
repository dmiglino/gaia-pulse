from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.pantry import PantryMovement, PantryStock
from app.repositories.food_repo import FoodRepository
from app.repositories.pantry_repo import PantryMovementRepository, PantryStockRepository
from app.schemas.pantry import PurchaseRequest, StockAdjustRequest


class PantryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.stock_repo = PantryStockRepository(db)
        self.movement_repo = PantryMovementRepository(db)
        self.food_repo = FoodRepository(db)

    def get_stock(
        self, household_id: int, search: str | None = None, category: str | None = None
    ) -> list:
        return self.stock_repo.get_household_stock(household_id, search=search, category=category)

    def get_low_stock(self, household_id: int) -> list:
        return self.stock_repo.get_low_stock(household_id)

    def process_purchase(
        self, household_id: int, user_id: int, request: PurchaseRequest
    ) -> list[PantryMovement]:
        """Register a purchase: creates movements and updates stock for each item."""
        movements = []
        now = datetime.now(timezone.utc)
        for item in request.items:
            food = self.food_repo.get_or_create(item.food_name)
            self.stock_repo.upsert_stock(
                household_id=household_id,
                food_item_id=food.id,
                delta=item.quantity,
                unit=item.unit,
            )
            movement = self._make_movement(
                household_id=household_id,
                user_id=user_id,
                food_item_id=food.id,
                movement_type="purchase",
                quantity=item.quantity,
                unit=item.unit,
                timestamp=now,
                notes=request.notes,
            )
            movements.append(movement)

        self.db.flush()
        self.db.commit()
        return movements

    def adjust_stock(
        self, household_id: int, user_id: int, request: StockAdjustRequest
    ) -> PantryMovement:
        """Apply a stock adjustment (consumption, manual correction, discard)."""
        food = self.food_repo.get_or_create(request.food_name)
        sign = -1.0 if request.movement_type in ("consumption", "discard") else 1.0
        self.stock_repo.upsert_stock(
            household_id=household_id,
            food_item_id=food.id,
            delta=sign * request.quantity,
            unit=request.unit,
        )
        movement = self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food.id,
            movement_type=request.movement_type,
            quantity=request.quantity,
            unit=request.unit,
            notes=request.notes,
        )
        self.db.flush()
        self.db.commit()
        return movement

    def adjust_stock_by_id(
        self,
        household_id: int,
        user_id: int,
        stock_id: int,
        quantity: float,
        movement_type: str,
        notes: str | None = None,
    ) -> PantryStock | None:
        """Adjust an existing stock row identified by its id.

        Unlike :meth:`adjust_stock`, ``adjustment`` means *set the quantity to
        this absolute value* (which is what the pantry grid's "Set" option
        offers); ``purchase`` adds, ``consumption``/``discard`` subtract. The
        recorded movement always carries the magnitude of the actual change.

        Returns ``None`` when the row does not belong to *household_id*.
        """
        stock = self.stock_repo.get_for_household(household_id, stock_id)
        if stock is None:
            return None

        if movement_type == "adjustment":
            delta = quantity - float(stock.current_quantity)
        elif movement_type in ("consumption", "discard"):
            delta = -quantity
        else:
            delta = quantity

        self.stock_repo.upsert_stock(
            household_id=household_id,
            food_item_id=stock.food_item_id,
            delta=delta,
            unit=stock.unit,
        )
        self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=stock.food_item_id,
            movement_type=movement_type,
            quantity=abs(delta),
            unit=stock.unit,
            notes=notes,
        )
        self.db.flush()
        self.db.commit()
        return stock

    def record_consumption(
        self,
        household_id: int,
        user_id: int,
        food_name: str,
        quantity: float,
        unit: str,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> PantryMovement | None:
        """Record consumption of a pantry item (typically triggered from meal logging)."""
        food = self.food_repo.find_by_name(food_name)
        if not food:
            return None
        if not self.stock_repo.get_by_food_item(household_id, food.id):
            return None

        self.stock_repo.upsert_stock(
            household_id=household_id,
            food_item_id=food.id,
            delta=-quantity,
            unit=unit,
        )
        movement = self._make_movement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food.id,
            movement_type="consumption",
            quantity=quantity,
            unit=unit,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.flush()
        return movement

    def get_movements(
        self, household_id: int, limit: int = 50, offset: int = 0
    ) -> list:
        return self.movement_repo.get_household_movements(household_id, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_movement(
        self,
        household_id: int,
        user_id: int,
        food_item_id: int,
        movement_type: str,
        quantity: float,
        unit: str,
        timestamp: datetime | None = None,
        notes: str | None = None,
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
    ) -> PantryMovement:
        movement = PantryMovement(
            household_id=household_id,
            user_id=user_id,
            food_item_id=food_item_id,
            movement_type=movement_type,
            quantity=quantity,
            unit=unit,
            timestamp=timestamp or datetime.now(timezone.utc),
            notes=notes,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.add(movement)
        return movement
