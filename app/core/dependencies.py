"""FastAPI dependency injection: session auth, DB, current user."""
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_session_token
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_repo import UserRepository

settings = get_settings()


def get_current_user_id(
    request: Request,
    session_token: str | None = Cookie(default=None, alias="gaiapulse_session"),
) -> int | None:
    # Try cookie name from settings
    token = request.cookies.get(settings.session_cookie_name) or session_token
    if not token:
        return None
    return decode_session_token(token)


def require_auth(
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> User:
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    user = UserRepository(db).get(user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


# Type aliases for cleaner route signatures
CurrentUser = Annotated[User, Depends(require_auth)]
DB = Annotated[Session, Depends(get_db)]
OptionalUserID = Annotated[int | None, Depends(get_current_user_id)]
