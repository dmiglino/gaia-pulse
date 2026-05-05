from fastapi import APIRouter, HTTPException

from app.core.dependencies import DB, CurrentUser
from app.schemas.suggestion import (
    RecommendationPreferenceCreate,
    RecommendationPreferenceRead,
    SuggestionFeedback,
    SuggestionRead,
)
from app.services.suggestion_service import SuggestionService

router = APIRouter()


@router.get("/", response_model=list[SuggestionRead])
def get_suggestions(current_user: CurrentUser, db: DB) -> list[SuggestionRead]:
    svc = SuggestionService(db)
    return [
        SuggestionRead.model_validate(s)
        for s in svc.get_pending(current_user.id, current_user.household_id)
    ]


@router.post("/{suggestion_id}/feedback", response_model=SuggestionRead)
def respond_to_suggestion(
    suggestion_id: int,
    feedback: SuggestionFeedback,
    current_user: CurrentUser,
    db: DB,
) -> SuggestionRead:
    svc = SuggestionService(db)
    suggestion = svc.respond_to_suggestion(suggestion_id, feedback, current_user.id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return SuggestionRead.model_validate(suggestion)


@router.get("/preferences", response_model=list[RecommendationPreferenceRead])
def get_preferences(current_user: CurrentUser, db: DB) -> list[RecommendationPreferenceRead]:
    svc = SuggestionService(db)
    return [
        RecommendationPreferenceRead.model_validate(p)
        for p in svc.get_user_preferences(current_user.id)
    ]


@router.post("/preferences", status_code=201)
def save_preference(
    data: RecommendationPreferenceCreate, current_user: CurrentUser, db: DB
) -> dict:
    svc = SuggestionService(db)
    svc.save_preference(current_user.id, data)
    return {"status": "saved"}


@router.post("/generate")
def trigger_generation(current_user: CurrentUser, db: DB) -> dict:
    """Manually trigger suggestion generation for the current user."""
    from app.recommendations.engine import RecommendationEngine
    engine = RecommendationEngine()
    new_suggestions = engine.generate_for_user(db, current_user, limit=10)
    return {"generated": len(new_suggestions)}
