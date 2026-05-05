from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

settings = get_settings()

_serializer = URLSafeTimedSerializer(settings.app_secret_key, salt="session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_session_token(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def decode_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age_seconds)
        return int(data["user_id"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None
