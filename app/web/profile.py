from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.services.suggestion_service import SuggestionService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "active", "very_active"}
_MAX_NAME_LEN = 80
_MAX_LIST_ITEM_LEN = 100
_MAX_LIST_ITEMS = 30


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
    return templates.TemplateResponse("profile/index.html", ctx)


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
