from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: Session) -> None:
        super().__init__(User, db)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return self.db.scalar(stmt)

    def get_household_users(self, household_id: int) -> list[User]:
        #: `ORDER BY` explícito: sin él el orden de las filas es el que quiera el motor,
        #: y este listado alimenta selects de persona y la resolución de los intents del
        #: NLP. Un orden que cambia entre corridas es un dato distinto cada vez.
        stmt = (
            select(User)
            .where(User.household_id == household_id, User.is_active.is_(True))
            .order_by(User.id)
        )
        return list(self.db.scalars(stmt).all())

    def get_by_name_key(self, name_key: str, household_id: int) -> User | None:
        """Resolve a name key like 'diego' or 'rocio' to a User."""
        stmt = select(User).where(User.household_id == household_id, User.is_active.is_(True))
        users = list(self.db.scalars(stmt).all())
        name_key_lower = name_key.lower().strip()
        for user in users:
            if user.name.lower().startswith(name_key_lower) or name_key_lower in user.name.lower():
                return user
        return None
