from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class BloodAnalysis(Base):
    """One uploaded blood lab report for a user."""

    __tablename__ = "blood_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Lab metadata
    analysis_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    lab_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Extracted content
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured biomarker values.
    # Shape: {"hemoglobin": {"value": 14.2, "unit": "g/dL", "ref_min": 13.5,
    #          "ref_max": 17.5, "status": "normal", "display_name": "Hemoglobin",
    #          "category": "blood_count"}}
    values_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # LLM-generated narrative summary
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    parsing_method: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # "llm" / "regex" / "manual"
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="analyzed", index=True
    )  # pending / analyzed / error

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # relationships
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from app.models.user import User
    user: Mapped["User"] = relationship("User", back_populates="blood_analyses")

    def markers_with_status(self, status: str) -> dict[str, Any]:
        """Return only markers whose status matches (e.g. 'low', 'high')."""
        if not self.values_json:
            return {}
        return {k: v for k, v in self.values_json.items() if v.get("status") == status}

    def __repr__(self) -> str:
        return f"<BloodAnalysis id={self.id} user={self.user_id} date={self.analysis_date}>"
