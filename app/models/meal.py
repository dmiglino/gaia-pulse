from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.food import FoodItem
    from app.models.household import Household
    from app.models.user import User


class MealEvent(Base):
    """A meal occurrence shared in time/context — but participants eat independently."""

    __tablename__ = "meal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    meal_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="other"
    )  # breakfast/lunch/snack/dinner/other
    context: Mapped[str] = mapped_column(
        String(40), nullable=False, default="home"
    )  # home/outside/travel/restaurant/other
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    household: Mapped["Household"] = relationship("Household")
    participants: Mapped[list["MealParticipant"]] = relationship(
        "MealParticipant", back_populates="meal_event", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<MealEvent id={self.id} type={self.meal_type} ts={self.timestamp}>"


class MealParticipant(Base):
    """A specific user's participation in a meal event."""

    __tablename__ = "meal_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_event_id: Mapped[int] = mapped_column(
        ForeignKey("meal_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    portion_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    estimated_total_grams: Mapped[float | None] = mapped_column(Numeric(7, 1), nullable=True)
    hunger_before: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    satiety_after: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    meal_event: Mapped["MealEvent"] = relationship("MealEvent", back_populates="participants")
    user: Mapped["User"] = relationship("User", back_populates="meal_participations")
    items_consumed: Mapped[list["MealItemConsumed"]] = relationship(
        "MealItemConsumed", back_populates="meal_participant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<MealParticipant meal={self.meal_event_id} user={self.user_id}>"


class MealItemConsumed(Base):
    """A single food item consumed by a specific participant in a meal."""

    __tablename__ = "meal_items_consumed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_event_id: Mapped[int] = mapped_column(
        ForeignKey("meal_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meal_participant_id: Mapped[int] = mapped_column(
        ForeignKey("meal_participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_items.id", ondelete="SET NULL"), nullable=True
    )
    normalized_free_text_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    estimated_grams: Mapped[float | None] = mapped_column(Numeric(8, 1), nullable=True)
    preparation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    affects_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    meal_participant: Mapped["MealParticipant"] = relationship(
        "MealParticipant", back_populates="items_consumed"
    )
    food_item: Mapped["FoodItem | None"] = relationship("FoodItem")

    def __repr__(self) -> str:
        return f"<MealItemConsumed name={self.normalized_free_text_name!r} qty={self.quantity}>"
