from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.food import FoodItem
    from app.models.household import Household
    from app.models.user import User


class PantryStock(Base):
    """Current pantry inventory level per food item per household."""

    __tablename__ = "pantry_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_item_id: Mapped[int] = mapped_column(
        ForeignKey("food_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    current_quantity: Mapped[float] = mapped_column(Numeric(10, 3), default=0, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    low_stock_threshold: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    storage_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # relationships
    household: Mapped["Household"] = relationship("Household", back_populates="pantry_stock")
    food_item: Mapped["FoodItem"] = relationship("FoodItem")

    @property
    def is_low(self) -> bool:
        if self.low_stock_threshold is None:
            return float(self.current_quantity) == 0
        return float(self.current_quantity) <= float(self.low_stock_threshold)

    def __repr__(self) -> str:
        return f"<PantryStock item={self.food_item_id} qty={self.current_quantity} {self.unit}>"


class PantryMovement(Base):
    """Audit trail of all pantry changes (purchases, consumption, adjustments)."""

    __tablename__ = "pantry_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    food_item_id: Mapped[int] = mapped_column(
        ForeignKey("food_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movement_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # purchase / consumption / adjustment / discard
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # Optional link to the triggering entity (e.g., MealEvent)
    related_entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    household: Mapped["Household"] = relationship("Household", back_populates="pantry_movements")
    user: Mapped["User"] = relationship("User", back_populates="pantry_movements")
    food_item: Mapped["FoodItem"] = relationship("FoodItem")

    def __repr__(self) -> str:
        return (
            f"<PantryMovement type={self.movement_type} item={self.food_item_id} "
            f"qty={self.quantity} {self.unit}>"
        )
