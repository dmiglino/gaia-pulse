from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class NLPIngestionEvent(Base):
    """Records every natural-language or voice ingestion attempt with full audit trail."""

    __tablename__ = "nlp_ingestion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    input_type: Mapped[str] = mapped_column(String(20), nullable=False)  # text / audio
    original_input: Mapped[str] = mapped_column(Text, nullable=False)
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)  # for audio

    # Parsed result — list of intent dicts
    parsed_intent_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    parser_layer: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # rules / llm / combined

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending_confirmation", index=True
    )  # pending_confirmation / confirmed / edited_and_confirmed / discarded

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # relationships
    user: Mapped["User"] = relationship("User", back_populates="nlp_events")

    def __repr__(self) -> str:
        return f"<NLPIngestionEvent id={self.id} user={self.user_id} status={self.status}>"
