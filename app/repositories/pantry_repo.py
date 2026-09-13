from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, joinedload

from app.models.food import FoodItem
from app.models.pantry import PantryMovement, PantryStock
from app.repositories.base import BaseRepository


class PantryStockRepository(BaseRepository[PantryStock]):
    def __init__(self, db: Session) -> None:
        super().__init__(PantryStock, db)

    def get_household_stock(
        self, household_id: int, search: str | None = None, category: str | None = None
    ) -> list[PantryStock]:
        stmt = (
            select(PantryStock)
            .join(PantryStock.food_item)
            .where(PantryStock.household_id == household_id)
            .options(joinedload(PantryStock.food_item))
            .order_by(FoodItem.canonical_name)
        )
        if search:
            stmt = stmt.where(
                func.lower(FoodItem.canonical_name).contains(search.lower())
            )
        if category:
            stmt = stmt.where(FoodItem.category == category)
        return list(self.db.scalars(stmt).unique().all())

    def get_for_household(self, household_id: int, stock_id: int) -> PantryStock | None:
        """Return one stock row by id, scoped to the household that owns it."""
        stmt = (
            select(PantryStock)
            .where(
                and_(
                    PantryStock.id == stock_id,
                    PantryStock.household_id == household_id,
                )
            )
            .options(joinedload(PantryStock.food_item))
        )
        return self.db.scalar(stmt)

    def get_by_food_item(self, household_id: int, food_item_id: int) -> PantryStock | None:
        stmt = select(PantryStock).where(
            and_(
                PantryStock.household_id == household_id,
                PantryStock.food_item_id == food_item_id,
            )
        )
        return self.db.scalar(stmt)

    def get_low_stock(self, household_id: int) -> list[PantryStock]:
        """Return items at or below their low-stock threshold, or at zero."""
        stmt = (
            select(PantryStock)
            .join(PantryStock.food_item)
            .where(PantryStock.household_id == household_id)
            .options(joinedload(PantryStock.food_item))
        )
        all_stock = list(self.db.scalars(stmt).unique().all())
        return [s for s in all_stock if s.is_low]

    def upsert_stock(
        self,
        household_id: int,
        food_item_id: int,
        delta: float,
        unit: str,
    ) -> PantryStock:
        """Atomically update stock quantity by delta (positive=add, negative=consume)."""
        stock = self.get_by_food_item(household_id, food_item_id)
        if stock is None:
            stock = PantryStock(
                household_id=household_id,
                food_item_id=food_item_id,
                current_quantity=max(0.0, delta),
                unit=unit,
                updated_at=datetime.now(UTC),
            )
            self.db.add(stock)
        else:
            new_qty = float(stock.current_quantity) + delta
            stock.current_quantity = max(0.0, new_qty)
            stock.updated_at = datetime.now(UTC)
        self.db.flush()
        return stock


class PantryMovementRepository(BaseRepository[PantryMovement]):
    def __init__(self, db: Session) -> None:
        super().__init__(PantryMovement, db)

    def get_household_movements(
        self,
        household_id: int,
        limit: int = 50,
        offset: int = 0,
        movement_type: str | None = None,
    ) -> list[PantryMovement]:
        stmt = (
            select(PantryMovement)
            .where(PantryMovement.household_id == household_id)
            .options(joinedload(PantryMovement.food_item))
            .order_by(PantryMovement.timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        if movement_type:
            stmt = stmt.where(PantryMovement.movement_type == movement_type)
        return list(self.db.scalars(stmt).unique().all())
