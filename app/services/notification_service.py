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

    def get_category_counts(self, user_id: int, household_id: int) -> dict[str, int]:
        """Qué categorías tiene esta persona, y cuántas de cada una.

        La pantalla dibujaba seis pastillas de filtro escritas a mano en la plantilla,
        y de esas seis solo tres las escribe algún job: `suggestion`, `trend` e `info`
        eran tres filtros que no podían dar resultado nunca. Con esto las pastillas son
        las categorías que la persona realmente tiene.
        """
        return self.repo.get_category_counts(user_id, household_id)

    def _get_owned(
        self, notification_id: int, user_id: int, household_id: int
    ) -> Notification | None:
        """Fetch a notification only if the acting user is allowed to act on it.

        A notification is either addressed to one member (``user_id`` set) or to
        the whole household (``user_id`` null). Anything else belongs to someone
        outside this household: without this check the id in the URL was enough
        to read or dismiss it.
        """
        n = self.repo.get(notification_id)
        if not n:
            return None
        if n.user_id == user_id:
            return n
        if n.user_id is None and n.household_id == household_id:
            return n
        return None

    def mark_read(
        self, notification_id: int, user_id: int, household_id: int
    ) -> Notification | None:
        """Mark one notification read and hand it back, or ``None`` if not theirs.

        Returning the row (instead of a bool) is what lets the web layer render
        the updated card without reaching into the repository itself, which
        ``AGENTS.md`` forbids — and it avoids re-reading what was just loaded.
        """
        n = self._get_owned(notification_id, user_id, household_id)
        if not n:
            return None
        n.read_at = datetime.now(timezone.utc)
        self.db.commit()
        return n

    def mark_all_read(self, user_id: int, household_id: int) -> int:
        count = self.repo.mark_all_read(user_id, household_id)
        self.db.commit()
        return count

    def dismiss(self, notification_id: int, user_id: int, household_id: int) -> bool:
        n = self._get_owned(notification_id, user_id, household_id)
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
