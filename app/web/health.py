"""Health section — blood analysis upload and history."""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.clock import local_now
from app.core.dependencies import DB, CurrentUser
from app.services.blood_analysis_service import BloodAnalysisService
from app.web.helpers import get_template_context, templates

router = APIRouter()
logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
}
_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


@router.get("/", response_class=HTMLResponse)
def health_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    svc = BloodAnalysisService(db)
    analyses = svc.get_analyses_for_user(current_user.id)
    ctx = get_template_context(request, db, current_user)
    ctx["analyses"] = analyses
    return templates.TemplateResponse("health/index.html", ctx)


@router.post("/upload")
async def health_upload(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    file: UploadFile = File(...),
) -> RedirectResponse:
    # Validate MIME type
    mime = file.content_type or ""
    if mime not in _ALLOWED_MIME_TYPES:
        logger.warning(
            "Rejected blood analysis upload: unsupported type %r (user_id=%d)",
            mime,
            current_user.id,
        )
        return RedirectResponse(url="/health?error=unsupported_type", status_code=302)

    file_bytes = await file.read()
    if len(file_bytes) > _MAX_FILE_BYTES:
        return RedirectResponse(url="/health?error=file_too_large", status_code=302)

    svc = BloodAnalysisService(db)
    try:
        record = await svc.upload_and_analyze(
            user_id=current_user.id,
            file_bytes=file_bytes,
            mime_type=mime,
            filename=file.filename or "upload",
        )
        db.commit()
    except Exception:
        logger.exception("Blood analysis upload failed for user_id=%d", current_user.id)
        db.rollback()
        return RedirectResponse(url="/health?error=parse_failed", status_code=302)

    return RedirectResponse(url=f"/health/{record.id}", status_code=302)


@router.get("/{analysis_id}", response_class=HTMLResponse)
def health_detail(
    analysis_id: int,
    request: Request,
    current_user: CurrentUser,
    db: DB,
) -> Response:
    svc = BloodAnalysisService(db)
    analysis = svc.get_analysis(analysis_id, current_user.id)
    if analysis is None:
        return RedirectResponse(url="/health", status_code=302)

    ctx = get_template_context(request, db, current_user)
    ctx["analysis"] = analysis
    # Cuántos días tiene el panel: un análisis de hace tres años describe a otra
    # persona, y hasta ahora la pantalla mostraba su fecha sin decir nada más.
    # `None` cuando el parser no encontró fecha, que no es lo mismo que "hoy".
    # El `max(0, ...)` es porque la fecha la escribe el parser desde el PDF: una mal
    # leída puede caer en el futuro, y "hace -5 días" es peor que "hace 0 días".
    ctx["panel_age_days"] = (
        max(0, (local_now().date() - analysis.analysis_date).days)
        if analysis.analysis_date
        else None
    )

    # Prepare chart data: separate abnormal vs normal markers
    abnormal = []
    normal = []
    if analysis.values_json:
        for key, data in analysis.values_json.items():
            status = data.get("status", "normal")
            entry = {
                "key": key,
                "display_name": data.get("display_name", key),
                "value": data.get("value"),
                "unit": data.get("unit", ""),
                "ref_min": data.get("ref_min"),
                "ref_max": data.get("ref_max"),
                "status": status,
                "category": data.get("category", ""),
            }
            if status in ("low", "critical_low", "high", "critical_high"):
                abnormal.append(entry)
            else:
                normal.append(entry)

    ctx["abnormal_markers"] = abnormal
    ctx["normal_markers"] = normal
    return templates.TemplateResponse("health/detail.html", ctx)


@router.post("/{analysis_id}/delete")
def health_delete(
    analysis_id: int,
    request: Request,
    current_user: CurrentUser,
    db: DB,
) -> RedirectResponse:
    svc = BloodAnalysisService(db)
    svc.delete_analysis(analysis_id, current_user.id)
    db.commit()
    return RedirectResponse(url="/health", status_code=302)
