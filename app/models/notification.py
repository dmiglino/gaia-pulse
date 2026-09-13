from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Scope: can be household-level, user-level, or both
    household_id: Mapped[int | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    category: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # low_stock/suggestion/inactivity/metric_reminder/meal_reminder/sleep_reminder/
    # trend/info/reminder
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional link to the triggering entity
    related_entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 1-10
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="system"
    )  # system/job/suggestion/rule

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # relationships
    household: Mapped["Household | None"] = relationship(
        "Household",
        back_populates="notifications",
        foreign_keys=[household_id],
    )
    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="notifications",
        foreign_keys=[user_id],
    )

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def is_dismissed(self) -> bool:
        return self.dismissed_at is not None

    def __repr__(self) -> str:
        return f"<Notification id={self.id} category={self.category} title={self.title!r:.40}>"
