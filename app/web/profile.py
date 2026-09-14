from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.learning_service import LearningService
from app.services.suggestion_service import SuggestionService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "active", "very_active"}
_MAX_NAME_LEN = 80
_MAX_LIST_ITEM_LEN = 100
_MAX_LIST_ITEMS = 30
#: Los topes de `behavior_signals.entity_type` (40) y `.entity_name` (200). El recorte va
#: en el borde y no en el servicio porque acá es donde entra texto de un formulario, igual
#: que `item_name` en `save_preference`: sin él, un POST a mano con 10 MB en un campo se
#: normaliza y se compara contra cada señal de la persona antes de no encontrar nada.
_MAX_SUBJECT_TYPE_LEN = 40
_MAX_SUBJECT_NAME_LEN = 200


def _parse_csv_list(raw: str) -> list[str]:
    """Parse a comma-separated string into a cleaned list, with per-item length guards."""
    return [item[:_MAX_LIST_ITEM_LEN] for item in (x.strip() for x in raw.split(",")) if item][
        :_MAX_LIST_ITEMS
    ]


@router.get("/", response_class=HTMLResponse)
def profile_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    ctx = get_template_context(request, db, current_user)
    svc = SuggestionService(db)
    ctx["preferences"] = svc.get_user_preferences(current_user.id)
    # Lo que el motor aprendió solo, que hasta acá no se podía ver desde ninguna pantalla:
    # `behavior_signals` no tenía **ninguna** lectura de usuario. Va en el perfil y no en
    # `/suggestions/` porque es un dato de la persona —y se corrige acá, al lado de las
    # restricciones alimentarias, que son el veto de verdad—.
    ctx["learned"] = LearningService(db).learned_profile(current_user.id)
    return templates.TemplateResponse("profile/index.html", ctx)


@router.post("/learned/forget")
def forget_learned(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    subject_type: str = Form(default=""),
    subject_name: str = Form(default=""),
) -> Response:
    """Olvidar lo aprendido sobre un sujeto y devolver el panel al día.

    Los dos campos se recortan acá en vez de declararse con `Form(max_length=...)`: un
    `max_length` de FastAPI contesta 422 con el valor recibido dentro de `detail[0].input`,
    o sea que un texto demasiado largo vuelve al navegador en la respuesta de error. Un
    recorte no tiene ese problema y además hace lo que corresponde con un nombre de sujeto,
    que es un dato acotado por la columna que lo guarda.

    Con HTMX se devuelve el panel recalculado —y no un flash— porque el objetivo del gesto
    es ver que la fila ya no está; el mensaje viaja adentro del mismo fragmento, que es la
    única forma de que se vea cuando el swap reemplaza una parte de la página y no la
    página. Sin JS, el redirect de siempre con el mensaje en el flash.

    El mensaje nombra el sujeto **como estaba guardado** (`Forgotten.subject_name`) y no lo
    que llegó en el formulario: la comparación es por forma normalizada, así que un
    `PÓLLO!!!` escrito a mano borra las filas de "pollo" — y contestar "Olvidado: PÓLLO!!!"
    sería devolverle a la pantalla un texto que la persona nunca guardó, sobre una acción
    que no se puede deshacer.
    """
    subject_type = subject_type.strip()[:_MAX_SUBJECT_TYPE_LEN]
    subject_name = subject_name.strip()[:_MAX_SUBJECT_NAME_LEN]

    svc = LearningService(db)
    forgotten = svc.forget(current_user.id, subject_type, subject_name)
    deleted = forgotten.deleted
    if deleted:
        message = _(
            "Forgotten: %(subject)s. If it happens again, it gets learned again.",
            subject=forgotten.subject_name,
        )
        category = "success"
    else:
        # Cero borrados no es un error: puede ser un doble clic, o una fila que se fue con
        # otra pestaña. Lo que no puede es decir "listo" sobre algo que no pasó.
        message = _("There was nothing left to forget about that.")
        category = "info"

    if not request.headers.get("HX-Request"):
        response = RedirectResponse(url="/profile/", status_code=302)
        set_flash(response, message, category)
        return response

    ctx = get_template_context(request, db, current_user)
    ctx["learned"] = svc.learned_profile(current_user.id)
    ctx["learned_message"] = message
    ctx["learned_message_tone"] = "ok" if deleted else "info"
    return templates.TemplateResponse("profile/partials/learned.html", ctx)


@router.post("/update")
def profile_update(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    name: Annotated[str, Form(max_length=_MAX_NAME_LEN)] = "",
    height_cm: Annotated[str, Form(max_length=10)] = "",
    target_weight_kg: Annotated[str, Form(max_length=10)] = "",
    baseline_activity_level: Annotated[str, Form(max_length=20)] = "moderate",
    dietary_restrictions: Annotated[str, Form(max_length=2000)] = "",
    impossible_activities: Annotated[str, Form(max_length=2000)] = "",
) -> RedirectResponse:
    errors: list[str] = []

    if name:
        current_user.name = name.strip()[:_MAX_NAME_LEN]

    if height_cm:
        try:
            val = float(height_cm)
            if 50.0 <= val <= 280.0:
                current_user.height_cm = val
            else:
                errors.append(_("Height must be between 50 and 280 cm."))
        except ValueError:
            errors.append(_("Height must be a number."))

    if target_weight_kg:
        try:
            val = float(target_weight_kg)
            if 20.0 <= val <= 500.0:
                current_user.target_weight_kg = val
            else:
                errors.append(_("Weight must be between 20 and 500 kg."))
        except ValueError:
            errors.append(_("Target weight must be a number."))

    if baseline_activity_level in _VALID_ACTIVITY_LEVELS:
        current_user.baseline_activity_level = baseline_activity_level
    else:
        errors.append(_("Invalid activity level: %(level)s.", level=baseline_activity_level))

    # Sin condición, a propósito: con `if dietary_restrictions:` vaciar la casilla no
    # borraba nada, así que una restricción alimentaria — el filtro que decide qué
    # comida se puede sugerir — no se podía sacar nunca desde la interfaz. La lista
    # que llega es la lista que queda, y vacío significa vacío.
    current_user.dietary_restrictions_json = _parse_csv_list(dietary_restrictions)
    current_user.impossible_activities_json = _parse_csv_list(impossible_activities)

    # Con la barra final, que es la ruta real: `/profile` existe solo como el 307 de
    # `redirect_slashes`, así que guardar el perfil costaba tres viajes en vez de dos.
    response = RedirectResponse(url="/profile/", status_code=302)
    if errors:
        # `errors` se armaba y se tiraba: una altura fuera de rango no se guardaba y
        # la pantalla no decía nada. Es todo o nada porque `get_db` cierra la sesión
        # sin commitear, así que un solo campo inválido descarta también los válidos
        # — y eso hay que decirlo, no dejarlo adivinar.
        db.rollback()
        set_flash(response, " ".join([*errors, _("Nothing was saved.")]), "error")
    else:
        db.commit()
        set_flash(response, _("Profile saved."), "success")
    return response
