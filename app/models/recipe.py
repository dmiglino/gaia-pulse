from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # null = global recipe, non-null = household-specific
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    portions: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    steps_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    tags_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    estimated_nutrition_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    pantry_compatible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prep_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cuisine: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Recipe id={self.id} name={self.name!r}>"
