from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import DB, OptionalUserID, get_current_user_id
from app.core.security import create_session_token
from app.db.session import get_db
from app.i18n import _
from app.services.auth_service import AuthService
from app.web.helpers import templates

router = APIRouter()
settings = get_settings()


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user_id: OptionalUserID = None,
) -> Response:
    if user_id:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    svc = AuthService(db)
    user = svc.authenticate(email, password)
    if not user:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": _("Invalid email or password.")},
            status_code=401,
        )

    token = create_session_token(user.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
    return resp


@router.post("/logout")
@router.get("/logout")
def logout(response: Response) -> Response:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(key=settings.session_cookie_name)
    return resp
