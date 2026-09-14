from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


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
    user: Mapped["User"] = relationship("User", back_populates="blood_analyses")
    #: Cada marcador, su propia fila (fase 7.7). Antes vivía en `values_json`, un blob
    #: por panel: comparar el mismo marcador entre dos paneles significaba traer los dos
    #: blobs completos y comparar a mano en Python. `order_by` fija un orden estable para
    #: la pantalla — sin él, el orden de un `dict` no es un contrato.
    markers: Mapped[list["BloodMarker"]] = relationship(
        "BloodMarker",
        back_populates="analysis",
        cascade="all, delete-orphan",
        order_by="BloodMarker.marker_key",
    )

    def __repr__(self) -> str:
        return f"<BloodAnalysis id={self.id} user={self.user_id} date={self.analysis_date}>"


class BloodMarker(Base):
    """Un valor de laboratorio dentro de un panel: `analysis_id` + `marker_key` únicos.

    Lo que reemplaza a `BloodAnalysis.values_json`. La fila, no el blob, es lo que
    habilita una tendencia por SQL: `SELECT ... WHERE marker_key = 'ferritin' ORDER BY
    analysis_id` es una consulta; contra un JSON por panel era traer todos los paneles y
    comparar en Python.
    """

    __tablename__ = "blood_markers"
    __table_args__ = (
        UniqueConstraint("analysis_id", "marker_key", name="uq_blood_markers_analysis_marker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("blood_analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marker_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ref_min: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    ref_max: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="other")

    analysis: Mapped["BloodAnalysis"] = relationship("BloodAnalysis", back_populates="markers")

    def __repr__(self) -> str:
        return f"<BloodMarker id={self.id} analysis={self.analysis_id} key={self.marker_key}>"
