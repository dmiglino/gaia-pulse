"""Service for blood analysis CRUD and AI-powered parsing."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.integrations.blood_analysis_parser import analyze_file
from app.models.blood_analysis import BloodAnalysis, BloodMarker
from app.repositories.blood_repo import BloodAnalysisRepository

logger = logging.getLogger(__name__)


class BloodAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = BloodAnalysisRepository(db)

    async def upload_and_analyze(
        self,
        user_id: int,
        file_bytes: bytes,
        mime_type: str,
        filename: str,
    ) -> BloodAnalysis:
        """Parse a blood lab file and persist the structured result."""
        try:
            result = await analyze_file(file_bytes, mime_type, filename)
        except Exception:
            logger.exception("Failed to analyze blood file for user_id=%d", user_id)
            record = BloodAnalysis(
                user_id=user_id,
                file_name=filename,
                status="error",
            )
            self.db.add(record)
            self.db.flush()
            return record

        record = BloodAnalysis(
            user_id=user_id,
            analysis_date=result.analysis_date,
            lab_name=result.lab_name,
            file_name=filename,
            raw_text=result.raw_text,
            ai_summary=result.ai_summary,
            parsing_method=result.parsing_method,
            status="analyzed",
        )
        #: El parser sigue devolviendo un `dict` por marcador (fase 7.7 solo cambia cómo
        #: se guarda, no cómo se extrae): una fila por clave, no un blob por panel.
        for marker_key, data in result.values.items():
            record.markers.append(
                BloodMarker(
                    marker_key=marker_key,
                    value=data["value"],
                    unit=data.get("unit"),
                    ref_min=data.get("ref_min"),
                    ref_max=data.get("ref_max"),
                    status=data.get("status") or "unknown",
                    display_name=data.get("display_name") or marker_key.replace("_", " ").title(),
                    category=data.get("category") or "other",
                )
            )
        self.db.add(record)
        self.db.flush()
        return record

    def get_analyses_for_user(self, user_id: int) -> list[BloodAnalysis]:
        return self.repo.get_for_user(user_id)

    def get_analysis(self, analysis_id: int, user_id: int) -> BloodAnalysis | None:
        return self.repo.get_owned(analysis_id, user_id)

    def get_latest_analysis(self, user_id: int) -> BloodAnalysis | None:
        """El último panel legible, con su fecha.

        Es la que usa `UserContext`: sin la fila no hay `analysis_date`, y sin la
        fecha el motor no puede saber si está aconsejando sobre un panel de este año
        o de 2021.
        """
        return self.repo.get_latest_analyzed(user_id)

    #: Acá estaba `get_latest_values`, que devolvía `values_json` y tiraba la fila. Su
    #: único llamador era el motor, y desde que el panel viaja por el contexto con su
    #: fecha no le queda ninguno: dejarla "para quien solo quiera los valores" es dejar
    #: disponible justo la versión que causó el problema —aconsejar sin saber de cuándo
    #: es el análisis— para que el próximo la elija por ser la más corta.

    def update_analysis_date(self, analysis_id: int, user_id: int, new_date: date | None) -> bool:
        """Corregir la fecha de un panel ya cargado.

        El parser ancla la fecha a una etiqueta (`_extract_date`), pero un informe con
        una etiqueta que no reconoce, o sin ninguna, la deja en `None` — y hasta ahora
        la única forma de arreglar eso era borrar el panel y volver a subirlo.
        """
        record = self.get_analysis(analysis_id, user_id)
        if record is None:
            return False
        record.analysis_date = new_date
        self.db.flush()
        return True

    def delete_analysis(self, analysis_id: int, user_id: int) -> bool:
        record = self.get_analysis(analysis_id, user_id)
        if record is None:
            return False
        self.db.delete(record)
        self.db.flush()
        return True
