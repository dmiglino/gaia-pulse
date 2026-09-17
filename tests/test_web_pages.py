"""Smoke: cada página web responde 200 y rinde HTML de verdad.

Por qué existe, y por qué se adelantó a la Fase 5: la Fase 3 toca **29 plantillas**
para llevarlas a los tokens y a los macros, y hasta ahora nada rendía una sola de
esas páginas. `pytest` daba verde con `ui.empty_state` mal tipeado, con un `{% call
%}` sobre un macro que no acepta `caller`, o con una variable de contexto renombrada
— el error aparecía recién en el navegador, en tiempo de request.

Esto no valida el diseño: valida que la página **existe, autentica y se rinde**. El
fixture `client` de `conftest.py` llevaba ahí sin un solo llamador desde el primer
commit del repo.
"""

import pytest
from fastapi.testclient import TestClient

from app.models.user import User

# Rutas sin parámetros. `/health/{id}` y `/meals/{id}` necesitan una fila propia y
# van aparte; `/logout` y `/onboarding` redirigen a propósito.
PAGES = [
    "/",
    "/pantry/",
    "/pantry/movements",
    "/pantry/shopping",
    "/meals/",
    "/workouts/",
    "/body-metrics/",
    "/capture/",
    "/health/",
    "/suggestions/",
    "/dashboard/",
    "/history/",
    "/profile/",
    "/notifications/",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(authenticated_client: TestClient, path: str) -> None:
    resp = authenticated_client.get(path)
    assert resp.status_code == 200, f"{path} → {resp.status_code}"
    # `<html` y no solo un 200: un handler puede devolver 200 con un cuerpo vacío,
    # y una plantilla a medias también.
    assert "<html" in resp.text, path
    assert "</body>" in resp.text, f"{path}: el documento quedó cortado"


@pytest.mark.parametrize("path", PAGES)
def test_page_has_no_undefined_leaking_into_the_html(
    authenticated_client: TestClient, path: str
) -> None:
    """Jinja rinde una variable inexistente como cadena vacía, no como error.

    Pero un objeto mal pasado sí se imprime con su `repr`, y eso llega al usuario:
    `<object at 0x...>`, `Undefined`, `None` pelado dentro de un atributo. Esos son
    los que se ven.
    """
    body = authenticated_client.get(path).text
    for leak in ("<object ", " object at 0x", "jinja2.", "Undefined"):
        assert leak not in body, f"{path}: se filtró {leak!r} al HTML"


@pytest.mark.parametrize("path", PAGES)
def test_page_carries_the_theme_attribute(authenticated_client: TestClient, path: str) -> None:
    """El `data-theme` del servidor es lo que evita el flash de tema al cargar.

    Si una plantilla dejara de extender el shell canónico, el atributo desaparece y
    el modo oscuro parpadea en blanco en cada navegación — un síntoma que en una
    revisión visual es fácil de atribuir a otra cosa.
    """
    assert 'data-theme="light"' in authenticated_client.get(path).text, path


def test_page_routes_redirect_anonymous_visitors_to_login(client: TestClient) -> None:
    """La otra mitad del ruteo dual de `AGENTS.md`: las páginas redirigen, no 401."""
    for path in PAGES:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 307), f"{path} → {resp.status_code}"
        assert resp.headers["location"] == "/login", path


def test_login_page_renders(client: TestClient) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'data-theme="light"' in resp.text


def test_error_pages_render_as_full_app_pages(client: TestClient) -> None:
    """El 404 pasa por el handler de `app/main.py`, cuyo contexto es solo
    `{"request": request}` — el caso que rompía cuando los macros dependían de un
    `{% import %}` por plantilla."""
    resp = client.get("/no-such-page")
    assert resp.status_code == 404
    assert 'data-theme="light"' in resp.text
    assert "<html" in resp.text


def test_onboarding_gate_redirects_a_user_who_has_not_finished(
    client: TestClient, diego: User
) -> None:
    """El gate de onboarding: sin él, un usuario nuevo cae en un Home vacío sin
    saber qué hacer. Se prueba acá y no en la Fase 5 porque el barrido visual toca
    `base.html`, que es donde vive el contexto que lo dispara."""
    from app.core.config import get_settings
    from app.core.security import create_session_token

    diego.onboarding_completed = False
    client.cookies.set(get_settings().session_cookie_name, create_session_token(diego.id))
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("/onboarding")
