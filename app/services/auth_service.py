from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreate


class AuthService:
    def __init__(self, db: Session) -> None:
        self.repo = UserRepository(db)
        self.db = db

    def authenticate(self, email: str, password: str) -> User | None:
        user = self.repo.get_by_email(email.lower())
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def create_user(self, data: UserCreate) -> User:
        user = User(
            household_id=data.household_id,
            name=data.name,
            email=data.email.lower(),
            password_hash=hash_password(data.password),
            height_cm=data.height_cm,
            target_weight_kg=data.target_weight_kg,
            baseline_activity_level=data.baseline_activity_level,
            birth_date=data.birth_date,
            sex=data.sex,
            avatar_color=data.avatar_color,
        )
        result = self.repo.create(user)
        self.db.commit()
        return result
