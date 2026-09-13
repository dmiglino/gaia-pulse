from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.dependencies import DB, CurrentUser
from app.i18n import _
from app.schemas.suggestion import RecommendationPreferenceCreate, SuggestionFeedback
from app.services.suggestion_service import SuggestionService
from app.web.flash import set_flash
from app.web.helpers import get_template_context, templates

router = APIRouter()

_VALID_FEEDBACK_STATUSES = {"accepted", "rejected", "snoozed", "dismissed"}
_VALID_PREFERENCE_SIGNALS = {"likes", "dislikes", "impossible", "possible_sometimes", "avoid", "preferred"}


def _redirect_with_flash(url: str, message: str, category: str) -> RedirectResponse:
    """Redirect to *url* carrying a one-shot message (non-HTMX fallback)."""
    response = RedirectResponse(url=url, status_code=302)
    set_flash(response, message, category)
    return response


def _back_to_suggestions(message: str, category: str) -> RedirectResponse:
    return _redirect_with_flash("/suggestions", message, category)


def _back_to_profile(message: str, category: str) -> RedirectResponse:
    return _redirect_with_flash("/profile", message, category)


@router.get("/", response_class=HTMLResponse)
def suggestions_index(request: Request, current_user: CurrentUser, db: DB) -> HTMLResponse:
    svc = SuggestionService(db)
    ctx = get_template_context(request, db, current_user)
    ctx["suggestions"] = svc.get_pending(current_user.id, current_user.household_id)
    ctx["preferences"] = svc.get_user_preferences(current_user.id)
    return templates.TemplateResponse("suggestions/index.html", ctx)


@router.post("/generate", response_class=HTMLResponse)
def generate_suggestions(request: Request, current_user: CurrentUser, db: DB) -> Response:
    """Run the recommendation engine on demand and swap the list back in."""
    svc = SuggestionService(db)
    svc.generate_for_user(current_user, limit=10)

    if not request.headers.get("HX-Request"):
        return _back_to_suggestions(_("Suggestions refreshed."), "success")

    ctx = get_template_context(request, db, current_user)
    ctx["suggestions"] = svc.get_pending(current_user.id, current_user.household_id)
    return templates.TemplateResponse("suggestions/partials/list.html", ctx)


@router.post("/{suggestion_id}/feedback")
def suggestion_feedback(
    request: Request,
    suggestion_id: int,
    current_user: CurrentUser,
    db: DB,
    status: str = Form(...),
    feedback_notes: str = Form(default=""),
) -> HTMLResponse:
    if status not in _VALID_FEEDBACK_STATUSES:
        ctx = get_template_context(request, db, current_user)
        ctx["error"] = _("Invalid feedback status: %(status)s.", status=status)
        ctx["suggestions"] = SuggestionService(db).get_pending(current_user.id, current_user.household_id)
        return templates.TemplateResponse("suggestions/index.html", ctx)

    svc = SuggestionService(db)
    svc.respond_to_suggestion(
        suggestion_id,
        SuggestionFeedback(status=status, feedback_notes=feedback_notes or None),
        current_user.id,
    )
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "suggestions/partials/dismissed.html",
            {"request": request, "suggestion_id": suggestion_id},
        )
    return _back_to_suggestions(_("Response saved."), "success")


@router.post("/preferences")
def save_preference(
    request: Request,
    current_user: CurrentUser,
    db: DB,
    item_type: str = Form(...),
    item_name: str = Form(...),
    preference_signal: str = Form(...),
) -> HTMLResponse:
    if preference_signal not in _VALID_PREFERENCE_SIGNALS:
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(
                "components/flash_messages.html",
                {"request": request, "messages": [{"type": "error", "text": _("Invalid signal: %(signal)s.", signal=preference_signal)}]},
            )
        return _back_to_profile(
            _("Invalid signal: %(signal)s.", signal=preference_signal), "error"
        )

    item_name = item_name.strip()[:200]
    item_type = item_type.strip()[:40]

    svc = SuggestionService(db)
    svc.save_preference(
        current_user.id,
        RecommendationPreferenceCreate(
            item_type=item_type,
            item_name=item_name,
            preference_signal=preference_signal,
        ),
    )
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/flash_messages.html",
            {"request": request, "messages": [{"type": "success", "text": _("Preference saved.")}]},
        )
    return _back_to_profile(_("Preference saved."), "success")
