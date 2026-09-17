"""Shared helpers for web route handlers: template rendering with auth context."""

import logging
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from babel import Locale, UnknownLocaleError
from babel.dates import format_date, format_time
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.clock import local_now, to_local
from app.core.config import get_settings
from app.i18n import setup_jinja2_i18n
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.services.notification_service import NotificationService
from app.web.actions import notification_action, suggestion_action
from app.web.exceptions import OnboardingRequiredError
from app.web.flash import read_flashes

logger = logging.getLogger(__name__)

_settings = get_settings()

# Paths that must stay reachable before onboarding is finished, or the gate
# would redirect the onboarding page to itself.
_ONBOARDING_EXEMPT_PREFIXES = ("/onboarding", "/login", "/logout", "/static")


class CompatJinja2Templates(Jinja2Templates):
    """Compatibility wrapper for Starlette TemplateResponse signature changes."""

    # Annotated on purpose: an unannotated override made every call site return
    # `Any`, and with `warn_return_any` that produced ~25 `no-any-return` errors
    # across the web layer. Starlette's `_TemplateResponse` subclasses
    # `HTMLResponse`, so this is the honest type, not a widening.
    def TemplateResponse(self, *args: Any, **kwargs: Any) -> HTMLResponse:  # type: ignore[override]
        # New signature (Starlette >=1.0): TemplateResponse(request, name, context, ...)
        if args and isinstance(args[0], Request):
            return super().TemplateResponse(*args, **kwargs)

        # Backward compatibility for old call style:
        # TemplateResponse(name, context, status_code=...)
        if not args:
            return super().TemplateResponse(*args, **kwargs)

        name = args[0]
        context = args[1] if len(args) > 1 else kwargs.get("context")
        if context is None:
            context = {}
        request = context.get("request")
        if request is None:
            raise ValueError("Template context must include 'request'")

        remaining_args = args[2:]
        return super().TemplateResponse(request, name, context, *remaining_args, **kwargs)


templates = CompatJinja2Templates(directory="app/templates")
# `app.core.csrf.csrf_protection` sets this on every request, before routing —
# it is the value it will also check the `csrf_token` form field against.
templates.env.globals["csrf_token"] = lambda request: request.state.csrf_token
templates.env.globals["locale"] = _settings.default_locale
# `home.html` ya llamaba a `now()` detrás de un `{% if now is defined %}`, y el
# global nunca había existido: el saludo quedaba clavado en "buenas tardes" y la
# fecha decía "Hoy". Es una función, no un valor, para que cada render lea la hora
# del momento y no la del arranque del proceso.
templates.env.globals["now"] = local_now
setup_jinja2_i18n(templates.env, _settings.default_locale)


@lru_cache(maxsize=1)
def _display_locale() -> Locale:
    """La locale de Babel con la que se formatean fechas y horas.

    Se degrada a inglés si `DEFAULT_LOCALE` trae algo que Babel no conoce, igual
    que `app/core/clock.py` se degrada a UTC: un valor mal escrito en el entorno
    no debería tumbar cada página con `UnknownLocaleError`.
    """
    try:
        return Locale.parse(_settings.default_locale)
    except (UnknownLocaleError, ValueError):
        logger.warning(
            "Locale '%s' desconocida para Babel; se formatea en inglés.",
            _settings.default_locale,
        )
        return Locale.parse("en")


def local_date(moment: datetime | date, fmt: str = "long") -> str:
    """La fecha de un instante, en hora local y en el idioma de la app.

    `strftime('%A, %B %-d')` devuelve los nombres del *locale del proceso*, así que
    en una app en `es_AR` el Home decía "Sunday, September 13". Babel ya es una
    dependencia (el catálogo de traducciones pasa por `babel.support`), así que el
    formateo localizado no agrega nada nuevo al stack.
    """
    when = to_local(moment) if isinstance(moment, datetime) else moment
    return format_date(when, format=fmt, locale=_display_locale())


def local_time(moment: datetime) -> str:
    """La hora de un instante, convertida a la timezone del hogar.

    Las columnas guardan UTC: sin la conversión, una comida de las 21:30 de acá se
    mostraba como 00:30 del día siguiente.

    El patrón es `H:mm` explícito y no `format='short'` porque el CLDR de `es_AR` pide
    12 horas ("9:30 p. m."), y acá la hora aparece en listas de 12px al lado de un
    contador: en Argentina se escribe 21:30 y ocupa la mitad.
    """
    return format_time(to_local(moment), format="H:mm", locale=_display_locale())


templates.env.filters["local_date"] = local_date
templates.env.filters["local_time"] = local_time

# Los macros de la v3, disponibles en toda plantilla sin `{% import %}`.
#
# `{% import %}` **no se hereda**: un `{% import 'components/ui.html' as ui %}` en
# `base.html` no existe dentro de los bloques de los hijos, así que sin esto las 29
# plantillas tendrían que repetir las mismas dos líneas y una olvidada sería un
# `UndefinedError` en tiempo de request (no en el arranque). Como globals, `ui.card()`
# y `ic.icon()` resuelven en cualquier plantilla, incluidos los parciales de HTMX y
# las páginas de error, que reciben un contexto de solo `{"request": request}`.
#
# Va **después** de `setup_jinja2_i18n`: `.module` ejecuta el cuerpo del template una
# vez, y ese cuerpo usa `_()` en los docstrings de los macros. Los `{% import %}` que
# ya existen en las plantillas siguen funcionando y ganan por scope local, así que
# esto no rompe nada de lo escrito hasta acá.
templates.env.globals["ui"] = templates.env.get_template("components/ui.html").module
templates.env.globals["ic"] = templates.env.get_template("components/icons.html").module
# `dm`: cómo se muestran los valores de enum de la base (tipo de comida, contexto,
# cantidades). Va con los otros dos porque el mismo rótulo aparece en el Home, en la
# lista, en el detalle y en el historial.
templates.env.globals["dm"] = templates.env.get_template("components/domain.html").module

# La acción primaria de una notificación o de una sugerencia. Va como global y no como
# macro de `dm` porque el mismo destino lo tienen que leer dos handlers — los `/act` —
# para saber a dónde redirigir después de anotar que la persona actuó: si el mapa
# viviera en una plantilla, el botón y el redirect serían dos verdades.
templates.env.globals["notification_action"] = notification_action
templates.env.globals["suggestion_action"] = suggestion_action


def query_int(raw: str | None) -> int | None:
    """Un parámetro de query numérico que puede llegar vacío.

    Los filtros de las pantallas son un `<form method="get">`, y un formulario manda
    **todos** sus campos, también los que el usuario dejó en blanco: `?user_id=` es
    "todo el hogar", no un error. Con `user_id: int | None` FastAPI responde 422 a
    esa misma URL, así que la conversión se hace acá.

    `isdecimal` y no `isdigit`: `"²".isdigit()` es `True` y `int("²")` explota, así que
    la versión con `isdigit` devolvía un 500 para `?user_id=²` — justo el tipo de URL
    editada a mano que este helper existe para absorber.
    """
    if raw is None or not raw.strip().lstrip("-").isdecimal():
        return None
    return int(raw)


def query_date(raw: str | None) -> date | None:
    """Un `<input type="date">` que puede llegar vacío o con basura, por lo mismo."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def get_template_context(request: Request, db: Session, current_user: User) -> dict:
    """Build the base template context with household and user data.

    ``base.html`` renders the notification badge and the flash messages on every
    page, so both belong here rather than in each individual route.

    Raises:
        OnboardingRequiredError: when the acting user has not completed onboarding
            and the request is not part of the onboarding flow itself.
    """
    if not current_user.onboarding_completed and not request.url.path.startswith(
        _ONBOARDING_EXEMPT_PREFIXES
    ):
        raise OnboardingRequiredError

    users = UserRepository(db).get_household_users(current_user.household_id)
    unread = NotificationService(db).get_unread_count(current_user.id, current_user.household_id)
    return {
        "request": request,
        "current_user": current_user,
        "users": users,
        "unread_notifications_count": unread,
        "flashes": read_flashes(request),
    }


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)
