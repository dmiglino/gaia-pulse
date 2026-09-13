"""Onboarding flow — shown once per user immediately after first login."""

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.models.body_metric import BodyMetricLog
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_SEXES = {"male", "female", "other", "prefer_not_to_say"}
_VALID_ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "active", "very_active"}


def _bounded_float(raw: str, low: float, high: float) -> float | None:
    """*raw* as a float inside [*low*, *high*], or ``None`` if it is neither.

    Las cuatro respuestas numéricas del wizard tenían cada una su `try/except
    ValueError: pass` y su `if low <= val <= high` sin rama `else`, así que un
    número mal escrito y un número fuera de rango terminaban en el mismo lugar:
    en ninguno. Acá los dos casos se vuelven un `None` que el llamador **tiene**
    que mirar.
    """
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if low <= value <= high else None


@router.get("/", response_class=HTMLResponse)
def onboarding_index(request: Request, current_user: CurrentUser, db: DB) -> Response:
    # If already done, redirect home
    if current_user.onboarding_completed:
        return RedirectResponse(url="/", status_code=302)
    ctx = get_template_context(request, db, current_user)
    ctx["current_year"] = date.today().year
    return templates.TemplateResponse("onboarding/index.html", ctx)


@router.post("/complete")
def onboarding_complete(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    # Step 1 — Personal info
    birth_year: Annotated[str, Form(max_length=4)] = "",
    sex: Annotated[str, Form(max_length=30)] = "",
    # Step 2 — Body measurements
    height_cm: Annotated[str, Form(max_length=10)] = "",
    weight_kg: Annotated[str, Form(max_length=10)] = "",
    target_weight_kg: Annotated[str, Form(max_length=10)] = "",
    # Step 3 — Activity & goals
    baseline_activity_level: Annotated[str, Form(max_length=20)] = "moderate",
    # Step 4 — Food preferences
    dietary_restrictions: Annotated[str, Form(max_length=500)] = "",
) -> RedirectResponse:
    #: Los campos que se mandaron y no se pudieron leer. A diferencia del perfil,
    #: acá no se descarta todo: el wizard se ve una sola vez y termina levantando
    #: el gate, así que negarse a terminarlo por un "1,70" en vez de "170" deja a
    #: la persona trabada o — como pasaba antes — la deja entrar con el dato
    #: perdido y sin que nadie se lo diga. Se guarda lo que se entiende, y lo que
    #: no se nombra con su rótulo y con dónde arreglarlo.
    unreadable: list[str] = []

    # Personal info
    if birth_year:
        try:
            year = int(birth_year)
        except ValueError:
            year = 0
        if 1900 <= year <= date.today().year:
            current_user.birth_date = date(year, 1, 1)
        else:
            unreadable.append(_("Year of birth"))

    #: Un valor inválido acá solo puede venir de un POST armado a mano: el paso 1
    #: ofrece cuatro radios y nada más. No hay nada que avisarle a quien lo hizo.
    if sex in _VALID_SEXES:
        current_user.sex = sex

    # Body measurements
    if height_cm:
        value = _bounded_float(height_cm, 50.0, 280.0)
        if value is None:
            unreadable.append(_("Height"))
        else:
            current_user.height_cm = value

    if weight_kg:
        value = _bounded_float(weight_kg, 20.0, 500.0)
        if value is None:
            unreadable.append(_("Weight"))
        else:
            db.add(
                BodyMetricLog(
                    user_id=current_user.id,
                    timestamp=datetime.now(timezone.utc),
                    weight_kg=value,
                )
            )

    if target_weight_kg:
        value = _bounded_float(target_weight_kg, 20.0, 500.0)
        if value is None:
            unreadable.append(_("Weight goal"))
        else:
            current_user.target_weight_kg = value

    # Activity level
    if baseline_activity_level in _VALID_ACTIVITY_LEVELS:
        current_user.baseline_activity_level = baseline_activity_level

    # Dietary restrictions
    if dietary_restrictions:
        items = [x.strip()[:100] for x in dietary_restrictions.split(",") if x.strip()][:20]
        if items:
            current_user.dietary_restrictions_json = items

    # Mark onboarding done
    current_user.onboarding_completed = True
    db.commit()

    response = RedirectResponse(url="/", status_code=302)
    if unreadable:
        set_flash(
            response,
            _(
                "All set! I could not read these, so I left them out: %(fields)s."
                " You can add them from your profile.",
                fields=", ".join(unreadable),
            ),
            "warning",
        )
    else:
        set_flash(response, _("All set. Welcome to GaiaPulse."), "success")
    return response
