from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/")
def get_dashboard(
    current_user: CurrentUser,
    db: DB,
    user_id: int | None = Query(None),
) -> dict:
    svc = DashboardService(db)
    uid = user_id or current_user.id
    return svc.get_user_dashboard_data(uid, current_user.household_id)
