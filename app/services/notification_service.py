from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.repositories.notification_repo import NotificationRepository
from app.schemas.notification import NotificationCreate


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = NotificationRepository(db)

    def create(self, data: NotificationCreate) -> Notification:
        notification = Notification(
            household_id=data.household_id,
            user_id=data.user_id,
            category=data.category,
            title=data.title,
            body=data.body,
            priority=data.priority,
            source_type=data.source_type,
            related_entity_type=data.related_entity_type,
            related_entity_id=data.related_entity_id,
        )
        self.db.add(notification)
        self.db.flush()
        self.db.commit()
        return notification

    def get_for_user(
        self,
        user_id: int,
        household_id: int,
        include_dismissed: bool = False,
        limit: int = 50,
        category: str | None = None,
    ) -> list[Notification]:
        return self.repo.get_for_user(
            user_id,
            household_id,
            include_dismissed=include_dismissed,
            limit=limit,
            category=category,
        )

    def get_unread_count(self, user_id: int, household_id: int) -> int:
        return self.repo.get_unread_count(user_id, household_id)

    def mark_read(self, notification_id: int) -> bool:
        n = self.repo.get(notification_id)
        if not n:
            return False
        n.read_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def mark_all_read(self, user_id: int, household_id: int) -> int:
        count = self.repo.mark_all_read(user_id, household_id)
        self.db.commit()
        return count

    def dismiss(self, notification_id: int) -> bool:
        n = self.repo.get(notification_id)
        if not n:
            return False
        n.dismissed_at = datetime.now(timezone.utc)
        self.db.commit()
        return True

    def snooze(self, notification_id: int, until: datetime) -> bool:
        n = self.repo.get(notification_id)
        if not n:
            return False
        n.snoozed_until = until
        self.db.commit()
        return True
