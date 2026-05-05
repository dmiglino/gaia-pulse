from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, db: Session) -> None:
        super().__init__(Notification, db)

    def get_for_user(
        self,
        user_id: int,
        household_id: int,
        include_dismissed: bool = False,
        limit: int = 50,
        category: str | None = None,
    ) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(
                or_(
                    Notification.user_id == user_id,
                    Notification.household_id == household_id,
                )
            )
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        if not include_dismissed:
            stmt = stmt.where(Notification.dismissed_at.is_(None))
        if category:
            stmt = stmt.where(Notification.category == category)
        return list(self.db.scalars(stmt).all())

    def get_unread_count(self, user_id: int, household_id: int) -> int:
        stmt = select(Notification).where(
            or_(
                Notification.user_id == user_id,
                Notification.household_id == household_id,
            ),
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
        return len(list(self.db.scalars(stmt).all()))

    def mark_all_read(self, user_id: int, household_id: int) -> int:
        stmt = (
            select(Notification)
            .where(
                or_(
                    Notification.user_id == user_id,
                    Notification.household_id == household_id,
                ),
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        notifications = list(self.db.scalars(stmt).all())
        now = datetime.utcnow()
        for n in notifications:
            n.read_at = now
        self.db.flush()
        return len(notifications)

    def has_recent_by_category(
        self,
        household_id: int,
        category: str,
        hours: int = 24,
        user_id: int | None = None,
    ) -> bool:
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        stmt = select(Notification).where(
            and_(
                Notification.category == category,
                Notification.created_at >= cutoff,
                or_(
                    Notification.household_id == household_id,
                    *(
                        [Notification.user_id == user_id]
                        if user_id
                        else []
                    ),
                ),
            )
        )
        return self.db.scalar(stmt) is not None
