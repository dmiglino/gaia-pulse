from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class BehaviorSignal(Base):
    """Persistent model for explicit and implicit learning signals.

    Used by the recommendation engine to learn individual and household
    preferences over time without training a model.
    """

    __tablename__ = "behavior_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What kind of signal
    signal_type: Mapped[str] = mapped_column(
        String(60), nullable=False, index=True
    )
    # El vocabulario vive en `app/recommendations/learning.py`
    # (`POSITIVE_SIGNAL_TYPES` / `NEGATIVE_SIGNAL_TYPES`), que es lo que el scorer lee y
    # el único módulo que escribe acá. Esta lista era de ejemplos aspiracionales
    # —`repeated_recipe`, `ingredient_pairing`, `rejected_activity`— que nadie escribió
    # nunca y que el scorer de la v1 sí leía; repetirla acá es la forma de que las dos
    # copias se separen otra vez.
    # Los que existen hoy:
    # accepted_suggestion / rejected_suggestion / ignored_suggestion / explicit_preference
    # repeated_meal_choice / repeated_purchase / repeated_activity

    # What entity this refers to
    entity_type: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )  # food/exercise/recipe/meal_type/cuisine/ingredient
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Signal value / context
    value: Mapped[float] = mapped_column(
        Numeric(6, 3), default=1.0, nullable=False
    )  # positive = positive signal, negative = negative, 0-1 intensity
    context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source of the signal
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # explicit/implicit/inferred
    source_entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="behavior_signals")

    def __repr__(self) -> str:
        return (
            f"<BehaviorSignal user={self.user_id} type={self.signal_type} "
            f"entity={self.entity_name!r} value={self.value}>"
        )
