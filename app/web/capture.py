from typing import Annotated

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.nlp_service import NLPService
from app.web.helpers import get_template_context, templates

router = APIRouter()

_MAX_INPUT_LEN = 2000


@router.get("/", response_class=HTMLResponse)
def capture_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    ctx = get_template_context(request, db, current_user)
    return templates.TemplateResponse("capture/index.html", ctx)


@router.post("/parse")
async def capture_parse(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    text: Annotated[str, Form(max_length=_MAX_INPUT_LEN)],
) -> HTMLResponse:
    """Parse text input and return preview partial (HTMX target)."""
    text = text.strip()
    ctx = get_template_context(request, db, current_user)
    if not text:
        ctx["error"] = _("Please enter some text to parse.")
        return templates.TemplateResponse("capture/preview_partial.html", ctx)

    svc = NLPService(db)
    event = await svc.parse_and_save(
        user_id=current_user.id,
        text=text,
        input_type="text",
    )
    ctx["nlp_event"] = event
    ctx["intents"] = event.parsed_intent_json or []
    return templates.TemplateResponse("capture/preview_partial.html", ctx)


@router.post("/transcribe")
async def capture_transcribe(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    audio: UploadFile,
) -> HTMLResponse:
    settings = get_settings()
    ctx = get_template_context(request, db, current_user)

    if not settings.stt_enabled:
        ctx["error"] = _("Voice input is not configured. Please use text input instead.")
        return templates.TemplateResponse("capture/preview_partial.html", ctx)

    audio_bytes = await audio.read()
    from app.integrations.stt.whisper_adapter import WhisperSTTAdapter
    stt = WhisperSTTAdapter()
    try:
        transcription = await stt.transcribe(audio_bytes, audio.content_type or "audio/webm")
    except RuntimeError as e:
        ctx["error"] = _("Transcription failed: %(error)s", error=str(e))
        return templates.TemplateResponse("capture/preview_partial.html", ctx)

    svc = NLPService(db)
    event = await svc.parse_and_save(
        user_id=current_user.id,
        text=transcription,
        input_type="audio",
        transcription=transcription,
    )
    ctx["nlp_event"] = event
    ctx["intents"] = event.parsed_intent_json or []
    return templates.TemplateResponse("capture/preview_partial.html", ctx)


@router.post("/confirm/{event_id}")
def capture_confirm(
    request: Request,
    event_id: int,
    current_user: CurrentUser,
    db: DB,
) -> HTMLResponse:
    svc = NLPService(db)
    result = svc.confirm_event(
        event_id=event_id,
        user_id=current_user.id,
        household_id=current_user.household_id,
    )

    if result.get("error"):
        ctx = get_template_context(request, db, current_user)
        ctx["error"] = result["error"]
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse("capture/preview_partial.html", ctx)
        return RedirectResponse(url="/capture", status_code=302)

    ctx = get_template_context(request, db, current_user)
    ctx["result"] = result
    ctx["success"] = result.get("success", False)
    ctx["notice"] = result.get("notice")

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("capture/confirm_result.html", ctx)
    return RedirectResponse(url="/", status_code=302)


@router.post("/discard/{event_id}")
def capture_discard(
    request: Request,
    event_id: int,
    current_user: CurrentUser,
    db: DB,
) -> HTMLResponse:
    svc = NLPService(db)
    svc.discard_event(event_id, current_user.id)

    if request.headers.get("HX-Request"):
        ctx = {"request": request, "discarded": True}
        return templates.TemplateResponse("capture/discard_result.html", ctx)
    return RedirectResponse(url="/capture", status_code=302)
