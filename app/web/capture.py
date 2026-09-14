import logging
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.models.nlp import NLPIngestionEvent
from app.services.nlp_service import NLPService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, query_date, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_INPUT_LEN = 2000

#: Con la barra final, como en `suggestions.py`: sin ella cada redirect se cobraba
#: primero un 307 de `redirect_slashes`.
_CAPTURE_URL = "/capture/"


def _redirect(url: str, message: str, category: str) -> RedirectResponse:
    """Volver a una página llevando el resultado, para el camino sin JS.

    Las cuatro salidas sin HTMX de este módulo redirigían en silencio: confirmar una
    captura sin JS te dejaba en Home sin decir si se guardó, y descartarla te dejaba
    en `/capture` sin decir que se descartó.
    """
    response = RedirectResponse(url=url, status_code=302)
    set_flash(response, message, category)
    return response


def _swap_error(template: str, ctx: dict[str, Any]) -> HTMLResponse:
    """Contestar un fallo con un fragmento que HTMX **sí** va a poner en pantalla.

    Por default HTMX no intercambia respuestas 4xx (`responseHandling` de htmx 2), así
    que el cuerpo traducido de estos fallos viajaba por el cable y no llegaba nunca al
    DOM: lo que veía la persona era el toast en inglés cableado en `app.js`, y el
    preview viejo seguía ahí con sus botones ya muertos.

    Acá el fallo *es* la respuesta que hay que mostrar — la captura ya no existe, el
    preview que está en pantalla está obsoleto —, así que se manda `HX-Reswap`, que
    `app.js` lee como "este error se muestra" y habilita el swap en `htmx:beforeSwap`.
    El 404 se mantiene para que la respuesta siga siendo honesta en el cable y en los
    tests.
    """
    return templates.TemplateResponse(
        template,
        ctx,
        status_code=404,
        headers={"HX-Reswap": "outerHTML", "HX-Retarget": "#nlp-preview"},
    )


def _preview_context(
    request: Request,
    db: DB,
    current_user: CurrentUser,
    svc: NLPService,
    event: NLPIngestionEvent,
) -> dict[str, Any]:
    """El contexto del preview, con la atribución ya resuelta.

    `intent_targets` sale de `NLPService.resolve_intent_targets`, el mismo método que
    usa `confirm_event` para decidir a quién le escribe. La plantilla ya no resuelve
    claves: recibe personas. Antes buscaba la clave cruda en un mapa keyeado en
    normalizado, así que la pantalla de consentimiento podía mostrar una atribución
    distinta de la que se iba a guardar.
    """
    ctx = get_template_context(request, db, current_user)
    intents = event.parsed_intent_json or []
    user_map = svc.household_user_map(current_user.household_id)
    ctx["nlp_event"] = event
    ctx["intents"] = intents
    ctx["intent_targets"] = [
        svc.resolve_intent_targets(intent, user_map, current_user) for intent in intents
    ]
    return ctx


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
    ctx = _preview_context(request, db, current_user, svc, event)
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
        #: El `str(e)` del adaptador iba entero a la pantalla: nombres de variables de
        #: entorno, el status del upstream, el motivo por el que falló la clave. Eso va
        #: al log del servidor, que es donde sirve; la persona recibe un mensaje fijo y
        #: traducido, y el camino que sí funciona.
        logger.warning("Speech-to-text failed for user %s: %s", current_user.id, e)
        ctx["error"] = _("I could not hear that. Try recording again, or write it instead.")
        return templates.TemplateResponse("capture/preview_partial.html", ctx)

    svc = NLPService(db)
    event = await svc.parse_and_save(
        user_id=current_user.id,
        text=transcription,
        input_type="audio",
        transcription=transcription,
    )
    ctx = _preview_context(request, db, current_user, svc, event)
    return templates.TemplateResponse("capture/preview_partial.html", ctx)


@router.post("/confirm/{event_id}")
def capture_confirm(
    request: Request,
    event_id: int,
    current_user: CurrentUser,
    db: DB,
    override_date: Annotated[str | None, Form()] = None,
) -> Response:
    svc = NLPService(db)
    result = svc.confirm_event(
        event_id=event_id,
        user_id=current_user.id,
        household_id=current_user.household_id,
        override_date=query_date(override_date),
    )

    if result.get("error"):
        # El servicio contesta el mismo `error` para "no existe", "no es tuyo" y "ya se
        # confirmó", y es un string en inglés que nunca pasó por el catálogo. Acá se
        # traduce, y con una sola redacción para los tres casos: la respuesta no tiene
        # que dejar distinguir un id inexistente de uno ajeno.
        message = _("That capture is no longer available.")
        if request.headers.get("HX-Request"):
            ctx = get_template_context(request, db, current_user)
            ctx["error"] = message
            return _swap_error("capture/preview_partial.html", ctx)
        return _redirect(_CAPTURE_URL, message, "error")

    ctx = get_template_context(request, db, current_user)
    ctx["result"] = result
    #: Solo el hecho, no el texto: el `notice` del servicio también es inglés crudo.
    ctx["nothing_to_save"] = bool(result.get("notice"))
    ctx["success"] = result.get("success", False)
    #: Cuántos intents escribieron una fila. Hace falta aparte de `success` porque
    #: "ninguno falló" no quiere decir "algo se guardó": un intent salteado o sin
    #: atribuir no falla, y la pantalla decía "Guardado · 1 cosa registrada" arriba de
    #: "nada que hacer con esto", con un tilde verde.
    ctx["saved_count"] = result.get("saved_count", 0)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("capture/confirm_result.html", ctx)
    if ctx["nothing_to_save"] or (ctx["success"] and not ctx["saved_count"]):
        #: La misma redacción que la tarjeta del camino con JS: es el mismo hecho.
        return _redirect(
            _CAPTURE_URL,
            _("I could not find anything to save in that. Nothing was written."),
            "warning",
        )
    if not ctx["saved_count"]:
        return _redirect(_CAPTURE_URL, _("None of that could be saved."), "error")
    if ctx["success"]:
        return _redirect("/", _("Saved."), "success")
    return _redirect("/", _("Some of it could not be saved."), "error")


@router.post("/discard/{event_id}")
def capture_discard(
    request: Request,
    event_id: int,
    current_user: CurrentUser,
    db: DB,
) -> Response:
    svc = NLPService(db)
    # `discard_event` devuelve `False` cuando el evento no existe o es de otra persona,
    # y ese booleano se descartaba: la pantalla decía "descartado, no se guardó nada"
    # igual, sobre algo que nunca tocó. Misma clase de mentira-en-el-fallo que el
    # feedback de sugerencias.
    discarded = svc.discard_event(event_id, current_user.id)

    if request.headers.get("HX-Request"):
        ctx: dict[str, Any] = {"request": request, "discarded": discarded}
        if not discarded:
            return _swap_error("capture/discard_result.html", ctx)
        return templates.TemplateResponse("capture/discard_result.html", ctx)
    if discarded:
        return _redirect(_CAPTURE_URL, _("Discarded. Nothing was saved."), "info")
    return _redirect(_CAPTURE_URL, _("That capture is no longer available."), "error")
