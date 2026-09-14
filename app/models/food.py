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

    # Macronutrientes **siempre por 100 g**, incluso cuando `base_unit` no es de peso.
    # Decía "(or per base_unit if non-weight)" y el catálogo sembrado dice lo contrario:
    # la banana tiene `base_unit="unit"` y 89 kcal, que es el valor por 100 g y no por
    # banana (una banana son ~105). Quien sume macros tiene que llegar a gramos primero
    # (`app/recommendations/context.py:_grams_of`), y como `serving_size_g` está NULL en
    # toda la base, un ítem contado en unidades no se puede convertir.
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
