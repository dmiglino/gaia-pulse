from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class FoodItem(Base):
    __tablename__ = "food_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(
        String(60), nullable=True
    )  # vegetable/fruit/protein/grain/dairy/fat/beverage/processed/other
    aliases_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # ["tomato", "tomate"]
    base_unit: Mapped[str] = mapped_column(String(30), default="g", nullable=False)  # g/ml/unit/serving
    serving_size_g: Mapped[float | None] = mapped_column(nullable=True)

    # Macronutrients per 100g (or per base_unit if non-weight)
    calories_per_100g: Mapped[float | None] = mapped_column(nullable=True)
    protein_g: Mapped[float | None] = mapped_column(nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(nullable=True)
    fat_g: Mapped[float | None] = mapped_column(nullable=True)
    fiber_g: Mapped[float | None] = mapped_column(nullable=True)

    # Extended nutrition (JSON for flexibility)
    macro_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    micro_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    perishable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    typical_shelf_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<FoodItem id={self.id} name={self.canonical_name!r}>"
