"""Service for blood analysis CRUD and AI-powered parsing."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.blood_analysis_parser import analyze_file
from app.models.blood_analysis import BloodAnalysis

logger = logging.getLogger(__name__)


class BloodAnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db

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
            values_json=result.values,
            ai_summary=result.ai_summary,
            parsing_method=result.parsing_method,
            status="analyzed",
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_analyses_for_user(self, user_id: int) -> list[BloodAnalysis]:
        return (
            self.db.query(BloodAnalysis)
            .filter(BloodAnalysis.user_id == user_id)
            .order_by(BloodAnalysis.analysis_date.desc(), BloodAnalysis.created_at.desc())
            .all()
        )

    def get_analysis(self, analysis_id: int, user_id: int) -> BloodAnalysis | None:
        return (
            self.db.query(BloodAnalysis)
            .filter(BloodAnalysis.id == analysis_id, BloodAnalysis.user_id == user_id)
            .first()
        )

    def get_latest_values(self, user_id: int) -> dict[str, Any]:
        """Return the most recent biomarker values for a user (for recommendations)."""
        latest = (
            self.db.query(BloodAnalysis)
            .filter(
                BloodAnalysis.user_id == user_id,
                BloodAnalysis.status == "analyzed",
                BloodAnalysis.values_json.isnot(None),
            )
            .order_by(BloodAnalysis.analysis_date.desc(), BloodAnalysis.created_at.desc())
            .first()
        )
        if latest and latest.values_json:
            return latest.values_json
        return {}

    def delete_analysis(self, analysis_id: int, user_id: int) -> bool:
        record = self.get_analysis(analysis_id, user_id)
        if record is None:
            return False
        self.db.delete(record)
        self.db.flush()
        return True
