from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import DB, CurrentUser
from app.core.security import create_session_token
from app.schemas.auth import LoginRequest, LoginResponse
from app.services.auth_service import AuthService

router = APIRouter()
settings = get_settings()


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest, response: Response, db: DB) -> LoginResponse:
    svc = AuthService(db)
    user = svc.authenticate(data.email, data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_session_token(user.id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
    return LoginResponse(
        user_id=user.id,
        name=user.name,
        email=user.email,
        household_id=user.household_id,
    )


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(key=settings.session_cookie_name)
    return {"status": "logged out"}


@router.get("/me", response_model=LoginResponse)
def me(current_user: CurrentUser) -> LoginResponse:
    return LoginResponse(
        user_id=current_user.id,
        name=current_user.name,
        email=current_user.email,
        household_id=current_user.household_id,
    )
