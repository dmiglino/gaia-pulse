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
        """Whether this cooldown already fired inside the window.

        Per-user and household-wide cooldowns are independent. They used to be
        ``or_``-ed together, and since a per-user reminder also carries
        ``household_id``, the household clause matched the *other* member's row
        and suppressed this user's notification for the whole window.
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        if user_id is not None:
            scope = and_(Notification.user_id == user_id)
        else:
            scope = and_(
                Notification.household_id == household_id,
                Notification.user_id.is_(None),
            )
        stmt = select(Notification).where(
            and_(
                Notification.category == category,
                Notification.created_at >= cutoff,
                scope,
            )
        )
        return self.db.scalar(stmt) is not None
