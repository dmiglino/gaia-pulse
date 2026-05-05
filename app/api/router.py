from fastapi import APIRouter

from app.api import (
    auth,
    body_metrics,
    dashboard,
    meals,
    nlp,
    notifications,
    pantry,
    suggestions,
    users,
    workouts,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(pantry.router, prefix="/pantry", tags=["pantry"])
api_router.include_router(meals.router, prefix="/meals", tags=["meals"])
api_router.include_router(workouts.router, prefix="/workouts", tags=["workouts"])
api_router.include_router(body_metrics.router, prefix="/body-metrics", tags=["body-metrics"])
api_router.include_router(suggestions.router, prefix="/suggestions", tags=["suggestions"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(nlp.router, prefix="/nlp", tags=["nlp"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
