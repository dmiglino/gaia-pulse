from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # scope
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user / household
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # content
    category: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # meal/activity/habit/pantry/shopping/variety/recovery/reminder
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # scoring / source
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 1-10
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0.5, nullable=False)  # 0-1
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # rule/trend/preference/stock/hybrid

    # status
    status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True
    )  # pending/accepted/rejected/snoozed/dismissed
    feedback_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # relationships
    household: Mapped["Household | None"] = relationship(
        "Household", back_populates="suggestions", foreign_keys=[household_id]
    )
    user: Mapped["User | None"] = relationship(
        "User", back_populates="suggestions", foreign_keys=[scope_user_id]
    )

    def __repr__(self) -> str:
        return f"<Suggestion id={self.id} category={self.category} status={self.status}>"


class RecommendationPreference(Base):
    """Explicit or inferred preference for a specific item/activity."""

    __tablename__ = "recommendation_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_type: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # food/recipe/exercise/cuisine/meal_type/ingredient
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    preference_signal: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # likes/dislikes/impossible/possible_sometimes/avoid/preferred
    strength: Mapped[float] = mapped_column(
        Numeric(4, 3), default=1.0, nullable=False
    )  # 0-1, 1=absolute
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="recommendation_preferences")

    def __repr__(self) -> str:
        return (
            f"<RecommendationPreference user={self.user_id} "
            f"type={self.item_type} name={self.item_name!r} signal={self.preference_signal}>"
        )
