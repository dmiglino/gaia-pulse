from fastapi import APIRouter, HTTPException, UploadFile

from app.core.dependencies import DB, CurrentUser
from app.schemas.nlp import NLPConfirmRequest, NLPEventRead, NLPParseRequest
from app.services.nlp_service import NLPService

router = APIRouter()


@router.post("/parse", response_model=NLPEventRead, status_code=201)
async def parse_text(
    data: NLPParseRequest, current_user: CurrentUser, db: DB
) -> NLPEventRead:
    svc = NLPService(db)
    event = await svc.parse_and_save(
        user_id=current_user.id,
        text=data.text,
        input_type=data.input_type,
    )
    return NLPEventRead.model_validate(event)


@router.post("/transcribe", response_model=NLPEventRead, status_code=201)
async def transcribe_and_parse(
    audio: UploadFile, current_user: CurrentUser, db: DB
) -> NLPEventRead:
    from app.core.config import get_settings
    settings = get_settings()

    if not settings.stt_enabled:
        raise HTTPException(
            status_code=503,
            detail="Voice input not configured. Set OPENAI_API_KEY and STT_PROVIDER=whisper.",
        )

    audio_bytes = await audio.read()
    content_type = audio.content_type or "audio/webm"

    # Transcribe
    from app.integrations.stt.whisper_adapter import WhisperSTTAdapter
    stt = WhisperSTTAdapter()
    try:
        transcription = await stt.transcribe(audio_bytes, content_type)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Parse transcription
    svc = NLPService(db)
    event = await svc.parse_and_save(
        user_id=current_user.id,
        text=transcription,
        input_type="audio",
        transcription=transcription,
    )
    return NLPEventRead.model_validate(event)


@router.post("/confirm")
async def confirm_event(
    data: NLPConfirmRequest, current_user: CurrentUser, db: DB
) -> dict:
    svc = NLPService(db)
    result = svc.confirm_event(
        event_id=data.event_id,
        user_id=current_user.id,
        household_id=current_user.household_id,
        edited_intents=data.edited_intents,
    )
    return result


@router.post("/discard/{event_id}")
def discard_event(event_id: int, current_user: CurrentUser, db: DB) -> dict:
    svc = NLPService(db)
    if not svc.discard_event(event_id, current_user.id):
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "discarded"}


@router.get("/{event_id}", response_model=NLPEventRead)
def get_event(event_id: int, current_user: CurrentUser, db: DB) -> NLPEventRead:
    svc = NLPService(db)
    event = svc.get_event(event_id)
    if not event or event.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Event not found")
    return NLPEventRead.model_validate(event)
