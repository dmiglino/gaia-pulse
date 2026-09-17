"""GaiaPulse — main FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.csrf import csrf_protection
from app.core.security_headers import security_headers
from app.web.exceptions import OnboardingRequiredError
from app.web.flash import FLASH_COOKIE_NAME, clear_flash
from app.web.helpers import templates
from app.web.router import web_router

settings = get_settings()
logging.basicConfig(
    level=logging.DEBUG if settings.app_debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Startup / shutdown lifecycle."""
    logger.info("Starting GaiaPulse v%s (%s)", settings.app_version, settings.app_env)

    from app.jobs.scheduler import start_scheduler, stop_scheduler

    start_scheduler()

    yield

    stop_scheduler()
    logger.info("GaiaPulse shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Shared household wellness app for Diego and Rocío",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # Static files
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse("app/static/images/icon-192.png", media_type="image/png")

    # Starlette wraps middlewares in registration order, and the *last*
    # registered ends up *outermost* (it wraps everything added before it) —
    # so this must be registered before the others, not after, for its 403
    # short-circuit to still pass back out through `security_headers` and
    # `privacy_headers` instead of skipping them entirely.
    app.middleware("http")(csrf_protection)

    @app.middleware("http")
    async def consume_flash(request: Request, call_next: Any) -> Any:
        """Clear the flash cookie once a full page has rendered its message.

        HTMX fragments do not include ``base.html``, so they must not eat a
        pending flash; neither must a redirect, which is what set it.
        """
        response = await call_next(request)
        if (
            FLASH_COOKIE_NAME in request.cookies
            and response.status_code < 300
            and "HX-Request" not in request.headers
        ):
            clear_flash(response)
        return response

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next: Any) -> Any:
        """No dejar datos de salud en el caché del navegador ni en un `Referer`.

        Cada página de esta app renderiza datos personales de quien la mira: peso,
        comidas, marcadores de sangre. Sin `Cache-Control`, el panel queda en el caché
        de disco y en el back/forward cache después de cerrar sesión — y este es un
        teléfono y una notebook compartidos entre dos personas.

        `Referrer-Policy` es preventivo: hoy los navegadores modernos ya no mandan la
        query string a otro origen, pero la app carga cuatro CDN y `?user_id=2` no tiene
        por qué salir de acá si ese default cambia.

        `/static/` queda afuera del `no-store`: ahí no hay nada personal y el caché es
        justamente lo que hace que la segunda carga sea rápida.
        """
        response = await call_next(request)
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.middleware("http")(security_headers)

    # API routes
    app.include_router(api_router)

    # Web (server-rendered) routes
    app.include_router(web_router)

    # Global exception handlers
    @app.exception_handler(401)
    async def unauthorized_handler(request: Request, exc: Any) -> Any:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

    @app.exception_handler(OnboardingRequiredError)
    async def onboarding_required_handler(request: Request, exc: Any) -> Any:
        """Send a user who never onboarded to the wizard before anything else.

        HTMX would swap a whole page into a fragment target, so an ``HX-Request``
        gets the client-side redirect header instead of a 302.
        """
        if "HX-Request" in request.headers:
            return Response(status_code=204, headers={"HX-Redirect": "/onboarding/"})
        return RedirectResponse(url="/onboarding/", status_code=302)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Any) -> Any:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return templates.TemplateResponse(
            request, "errors/404.html", {"request": request}, status_code=404
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc: Any) -> Any:
        logger.exception("Unhandled server error")
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        return templates.TemplateResponse(
            request, "errors/500.html", {"request": request}, status_code=500
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level="debug" if settings.app_debug else "info",
    )
