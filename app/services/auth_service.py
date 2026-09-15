import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import as_utc
from app.core.security import hash_password, verify_password
from app.i18n import _
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.repositories.password_reset_repo import PasswordResetTokenRepository
from app.repositories.user_repo import UserRepository
from app.schemas.user import UserCreate
from app.services.mail_service import send_email

#: Una hora: alcanza para revisar el mail sin dejar el link viejo dando vueltas.
RESET_TOKEN_TTL = timedelta(hours=1)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AuthService:
    def __init__(self, db: Session) -> None:
        self.repo = UserRepository(db)
        self.reset_repo = PasswordResetTokenRepository(db)
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

    def request_password_reset(self, email: str, build_link: Callable[[str], str]) -> None:
        """Crea un token de reset y manda el mail, si ese email existe.

        Responde igual (sin excepción, sin valor) si no existe: distinguir la
        respuesta le regalaría a quien la mire qué emails viven en la casa.
        """
        user = self.repo.get_by_email(email.lower())
        if not user or not user.is_active:
            return

        raw_token = secrets.token_urlsafe(32)
        self.reset_repo.create(
            PasswordResetToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
            )
        )
        self.db.commit()

        send_email(
            user.email,
            _("Reset your GaiaPulse password"),
            _(
                "Someone asked to reset your GaiaPulse password. Open this link "
                "within an hour to choose a new one:\n\n%(link)s\n\n"
                "If this wasn't you, ignore this email — your password stays the same."
            )
            % {"link": build_link(raw_token)},
        )

    def get_valid_reset_token(self, raw_token: str) -> PasswordResetToken | None:
        token = self.reset_repo.get_by_token_hash(_hash_token(raw_token))
        if not token or token.used_at is not None:
            return None
        if as_utc(token.expires_at) < datetime.now(UTC):
            return None
        return token

    def reset_password(self, raw_token: str, new_password: str) -> bool:
        token = self.get_valid_reset_token(raw_token)
        if not token:
            return False
        user = self.repo.get(token.user_id)
        if not user or not user.is_active:
            return False
        user.password_hash = hash_password(new_password)
        token.used_at = datetime.now(UTC)
        self.db.commit()
        return True
