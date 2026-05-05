"""Onboarding flow — shown once per user immediately after first login."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_SEXES = {"male", "female", "other", "prefer_not_to_say"}
_VALID_ACTIVITY_LEVELS = {"sedentary", "light", "moderate", "active", "very_active"}


@router.get("/", response_class=HTMLResponse)
def onboarding_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    # If already done, redirect home
    if current_user.onboarding_completed:
        return RedirectResponse(url="/", status_code=302)
    ctx = get_template_context(request, db, current_user)
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
    # Personal info
    if birth_year:
        try:
            year = int(birth_year)
            current_year = date.today().year
            if 1900 <= year <= current_year:
                current_user.birth_date = date(year, 1, 1)
        except ValueError:
            pass

    if sex in _VALID_SEXES:
        current_user.sex = sex

    # Body measurements
    if height_cm:
        try:
            val = float(height_cm)
            if 50.0 <= val <= 280.0:
                current_user.height_cm = val
        except ValueError:
            pass

    if weight_kg:
        from app.models.body_metric import BodyMetricLog
        from datetime import datetime, timezone
        try:
            val = float(weight_kg)
            if 20.0 <= val <= 500.0:
                log = BodyMetricLog(
                    user_id=current_user.id,
                    timestamp=datetime.now(timezone.utc),
                    weight_kg=val,
                )
                db.add(log)
        except ValueError:
            pass

    if target_weight_kg:
        try:
            val = float(target_weight_kg)
            if 20.0 <= val <= 500.0:
                current_user.target_weight_kg = val
        except ValueError:
            pass

    # Activity level
    if baseline_activity_level in _VALID_ACTIVITY_LEVELS:
        current_user.baseline_activity_level = baseline_activity_level

    # Dietary restrictions
    if dietary_restrictions:
        items = [
            x.strip()[:100]
            for x in dietary_restrictions.split(",")
            if x.strip()
        ][:20]
        if items:
            current_user.dietary_restrictions_json = items

    # Mark onboarding done
    current_user.onboarding_completed = True
    db.flush()
    db.commit()

    return RedirectResponse(url="/", status_code=302)
