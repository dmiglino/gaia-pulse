from fastapi import APIRouter

from app.web import (
    auth,
    body_metrics,
    capture,
    dashboard,
    health,
    history,
    home,
    meals,
    notifications,
    pantry,
    profile,
    suggestions,
    workouts,
)

web_router = APIRouter()

web_router.include_router(auth.router, tags=["web-auth"])
web_router.include_router(home.router, tags=["web-home"])
web_router.include_router(pantry.router, prefix="/pantry", tags=["web-pantry"])
web_router.include_router(meals.router, prefix="/meals", tags=["web-meals"])
web_router.include_router(workouts.router, prefix="/workouts", tags=["web-workouts"])
web_router.include_router(body_metrics.router, prefix="/body-metrics", tags=["web-metrics"])
web_router.include_router(capture.router, prefix="/capture", tags=["web-capture"])
web_router.include_router(health.router, prefix="/health", tags=["web-health"])
web_router.include_router(suggestions.router, prefix="/suggestions", tags=["web-suggestions"])
web_router.include_router(dashboard.router, prefix="/dashboard", tags=["web-dashboard"])
web_router.include_router(history.router, prefix="/history", tags=["web-history"])
web_router.include_router(profile.router, prefix="/profile", tags=["web-profile"])
web_router.include_router(notifications.router, prefix="/notifications", tags=["web-notifications"])
