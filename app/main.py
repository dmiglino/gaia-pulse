"""GaiaPulse — main FastAPI application factory."""
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
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
