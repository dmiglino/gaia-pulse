from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class BodyMetricLog(Base):
    __tablename__ = "body_metric_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    weight_kg: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    muscle_mass_kg: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    waist_cm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="body_metrics")

    def __repr__(self) -> str:
        return f"<BodyMetricLog id={self.id} user={self.user_id} weight={self.weight_kg}>"
