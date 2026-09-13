from fastapi import APIRouter, HTTPException, Query

from app.core.dependencies import DB, CurrentUser
from app.schemas.notification import NotificationRead
from app.services.notification_service import NotificationService

router = APIRouter()


@router.get("/", response_model=list[NotificationRead])
def get_notifications(
    current_user: CurrentUser,
    db: DB,
    include_dismissed: bool = Query(False),
    limit: int = Query(50, le=200),
    category: str | None = Query(None),
) -> list[NotificationRead]:
    svc = NotificationService(db)
    notifications = svc.get_for_user(
        current_user.id,
        current_user.household_id,
        include_dismissed=include_dismissed,
        limit=limit,
        category=category,
    )
    return [NotificationRead.model_validate(n) for n in notifications]


@router.get("/unread-count")
def get_unread_count(current_user: CurrentUser, db: DB) -> dict:
    svc = NotificationService(db)
    count = svc.get_unread_count(current_user.id, current_user.household_id)
    return {"unread_count": count}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, current_user: CurrentUser, db: DB) -> dict:
    svc = NotificationService(db)
    if not svc.mark_read(notification_id, current_user.id, current_user.household_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "read"}


@router.post("/read-all")
def mark_all_read(current_user: CurrentUser, db: DB) -> dict:
    svc = NotificationService(db)
    count = svc.mark_all_read(current_user.id, current_user.household_id)
    return {"marked_read": count}


@router.post("/{notification_id}/dismiss")
def dismiss(notification_id: int, current_user: CurrentUser, db: DB) -> dict:
    svc = NotificationService(db)
    if not svc.dismiss(notification_id, current_user.id, current_user.household_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "dismissed"}
