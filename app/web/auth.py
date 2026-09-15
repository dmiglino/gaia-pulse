from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import OptionalUserID
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
    remember: bool = Form(False),
    db: Session = Depends(get_db),
) -> Response:
    svc = AuthService(db)
    user = svc.authenticate(email, password)
    if not user:
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": _("Invalid email or password."),
                # Keep the email so only the password has to be retyped.
                "email_value": email,
            },
            status_code=401,
        )

    token = create_session_token(user.id)
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        # Sin `remember`, `max_age=None` deja la cookie de sesión: el navegador la
        # borra al cerrarse. El token firmado sigue aceptando hasta
        # `session_max_age_seconds` en `decode_session_token` de cualquier forma —
        # esto solo decide cuánto vive la cookie en disco, no cuánto vale la firma.
        max_age=settings.session_max_age_seconds if remember else None,
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


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> Response:
    return templates.TemplateResponse("auth/forgot_password.html", {"request": request})


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    svc = AuthService(db)
    svc.request_password_reset(
        email, lambda token: f"{settings.public_base_url}/reset-password/{token}"
    )
    # Misma respuesta exista o no el email: distinguir le regalaría a quien la
    # mire qué emails viven en la casa.
    return templates.TemplateResponse(
        "auth/forgot_password.html", {"request": request, "sent": True}
    )


@router.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)) -> Response:
    svc = AuthService(db)
    invalid = svc.get_valid_reset_token(token) is None
    return templates.TemplateResponse(
        "auth/reset_password.html",
        {"request": request, "token": token, "invalid": invalid},
    )


@router.post("/reset-password/{token}")
def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    svc = AuthService(db)
    if svc.get_valid_reset_token(token) is None:
        return templates.TemplateResponse(
            "auth/reset_password.html",
            {"request": request, "token": token, "invalid": True},
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "auth/reset_password.html",
            {"request": request, "token": token, "error": _("Passwords don't match.")},
        )

    if not svc.reset_password(token, password):
        return templates.TemplateResponse(
            "auth/reset_password.html",
            {"request": request, "token": token, "invalid": True},
        )

    return templates.TemplateResponse("auth/login.html", {"request": request, "reset_done": True})
