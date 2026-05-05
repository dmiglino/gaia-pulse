from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.notification import Notification
    from app.models.pantry import PantryMovement, PantryStock
    from app.models.suggestion import Suggestion
    from app.models.user import User


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Our Home")
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="UTC")
    settings_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="household")
    pantry_stock: Mapped[list["PantryStock"]] = relationship(
        "PantryStock", back_populates="household"
    )
    pantry_movements: Mapped[list["PantryMovement"]] = relationship(
        "PantryMovement", back_populates="household"
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        "Suggestion", back_populates="household", foreign_keys="Suggestion.household_id"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="household", foreign_keys="Notification.household_id"
    )

    def __repr__(self) -> str:
        return f"<Household id={self.id} name={self.name!r}>"
