"""Health section — blood analysis upload and history."""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError

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
_UPLOAD_CHUNK_BYTES = 256 * 1024

# Los cinco valores que `_determine_status` puede devolver son estos cuatro más
# `"normal"`. Cualquier otra cosa —`"unknown"`, o una cadena que el LLM haya inventado—
# significa que el marcador no se evaluó, y eso **no** es "en rango".
_ABNORMAL_STATUSES = ("low", "critical_low", "high", "critical_high")

# El `Content-Type` lo declara el cliente, así que la lista de MIME permitidos por sí
# sola no dice nada sobre lo que llegó. Estas son las firmas de los cinco formatos que
# el parser sabe leer; `None` es "no hay firma estable" (HEIC trae el `ftyp` en el
# offset 4, se chequea aparte).
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


def _looks_like(mime: str, head: bytes) -> bool:
    """Si los primeros bytes son coherentes con el tipo declarado."""
    if mime == "image/heic":
        return head[4:8] == b"ftyp"
    prefixes = _MAGIC_PREFIXES.get(mime)
    return prefixes is None or head.startswith(prefixes)


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

    # Por trozos y cortando en el límite: `await file.read()` de una sola vez traía el
    # cuerpo completo a memoria **antes** de comparar contra el tope, así que el límite
    # de 20 MB no acotaba nada.
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_FILE_BYTES:
            return RedirectResponse(url="/health?error=file_too_large", status_code=302)
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    if not _looks_like(mime, file_bytes[:16]):
        logger.warning(
            "Rejected blood analysis upload: %r content does not match its bytes (user_id=%d)",
            mime,
            current_user.id,
        )
        return RedirectResponse(url="/health?error=unsupported_type", status_code=302)

    svc = BloodAnalysisService(db)
    try:
        record = await svc.upload_and_analyze(
            user_id=current_user.id,
            file_bytes=file_bytes,
            mime_type=mime,
            filename=file.filename or "upload",
        )
        db.commit()
    except SQLAlchemyError as exc:
        # `logger.exception` sobre un error de SQLAlchemy imprime `str(exc)`, que incluye
        # el `[SQL: INSERT ...] [parameters: ...]` — o sea el `raw_text` del panel y sus
        # marcadores, datos de salud, en el log de la aplicación. Del error de base solo
        # se registra el tipo y el mensaje del driver, nunca la sentencia con sus valores.
        logger.error(
            "Blood analysis upload failed for user_id=%d: %s (%s)",
            current_user.id,
            type(exc).__name__,
            type(getattr(exc, "orig", None)).__name__,
        )
        db.rollback()
        return RedirectResponse(url="/health?error=parse_failed", status_code=302)
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
    # `None` en dos casos, y los dos significan "no se puede afirmar la antigüedad":
    # cuando el parser no encontró fecha, y cuando la que encontró está en el futuro.
    # Esto último antes se recortaba con `max(0, ...)`, y "hace 0 días" al lado de un
    # encabezado que dice "1 de enero de 2099" es una contradicción en pantalla donde
    # el dato más prominente es el falso. Si la fecha no se puede creer, no se muestra
    # la antigüedad.
    age = (local_now().date() - analysis.analysis_date).days if analysis.analysis_date else None
    ctx["panel_age_days"] = age if age is not None and age >= 0 else None

    # Los marcadores en tres grupos, no en dos. `_determine_status` devuelve `"unknown"`
    # para cualquier clave que no tenga rango de referencia (la tabla conoce 31, y la
    # Capa 2 acepta cualquier clave snake_case que el LLM emita), y `"unknown"` caía en
    # el `else`: la pantalla lo mostraba en la tarjeta verde "En rango" y afirmaba
    # "todos los marcadores están dentro de su rango" sobre un valor que **nunca
    # evaluó**. Eso es una afirmación clínica inventada.
    abnormal = []
    normal = []
    unevaluated = []
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
            if status in _ABNORMAL_STATUSES:
                abnormal.append(entry)
            elif status == "normal":
                normal.append(entry)
            else:
                unevaluated.append(entry)

    ctx["abnormal_markers"] = abnormal
    ctx["normal_markers"] = normal
    ctx["unevaluated_markers"] = unevaluated
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
