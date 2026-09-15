"""Que cada URL escrita en una plantilla exista, y que cada página exista en el smoke.

Los dos tests de acá cubren el mismo modo de falla, que es el que produjo cuatro de
los trece defectos de la tabla A del plan y las 139 traducciones faltantes de la 5.1:
**una lista mantenida a mano que deja de coincidir con el código y no avisa.** Una URL
mal escrita en un `hx-post` no rompe ningún test — rompe un botón, en el navegador, en
silencio; y una página nueva que nadie agrega a `PAGES` no está cubierta por el smoke
aunque el smoke esté verde.

`test_web_fragments.py` ya tenía media red: verifica que ninguna plantilla apunte a
`/api/`, que es la regla de enrutamiento dual de `AGENTS.md`. Pero una ruta web
inexistente pasa ese test sin problema, porque no empieza con `/api/`. Acá se resuelve
el objetivo de verdad, contra el router.

Las dos derivaciones usan API **pública**:

- `app.openapi()["paths"]`, para enumerar. Caminar `app.routes` a mano no sirve:
  las rutas web viven dentro de dos envoltorios `_IncludedRouter` cuyo `.routes` está
  vacío y cuyo `.original_router.routes` devuelve los paths **sin el prefijo**
  (`/badge`, no `/notifications/badge`). Hay un `effective_route_contexts()` que sí
  resuelve los prefijos, pero es privado de FastAPI.
- `Route.matches(scope)` de Starlette, para resolver. Es el mismo protocolo que usa el
  router en cada request, así que maneja el anidamiento solo y entiende los parámetros
  de path sin que haya que reimplementar la conversión a regex.
"""

import re
from pathlib import Path

import pytest
from starlette.routing import Match

from app.main import app
from tests.test_web_pages import PAGES

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

# Los comentarios de Jinja se sacan **antes** de extraer: `components/ui.html` documenta
# la regla de `attrs` con un ejemplo que contiene `hx-post="/meals/…/delete"`, una ruta
# que no existe ni tiene que existir. Sin este paso el test reporta un defecto fantasma
# en su propia documentación.
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)

TARGET_ATTR = re.compile(r'\b(hx-(?:get|post|put|patch|delete)|action)="([^"]*)"')
FORM_TAG = re.compile(r"<form\b[^>]*", re.S)
METHOD_ATTR = re.compile(r'\bmethod="(\w+)"')

# Interpolación de Jinja, en sus dos formas: dentro de un atributo (`{{ x }}`) y
# concatenada dentro de un argumento de macro (`' ~ x ~ '`). Todo parámetro de ruta de
# esta app es un entero, así que se sustituye por uno y `Route.matches` hace el resto.
JINJA_VALUE = re.compile(r"\{\{.*?\}\}|'\s*~.*?~\s*'")

# `components/ui.html` recibe el destino como parámetro (`day_person_filters(action,…)`),
# así que su valor no existe hasta que se rinde y no hay nada que resolver estáticamente.
# Sus dos llamadores pasan `/meals/` y `/workouts/`, y esos sí se resuelven acá porque el
# extractor los ve como literales en `meals/index.html` y `workouts/index.html`.
MACRO_PARAM_TARGETS = {"{{ action }}"}


def _iter_targets() -> list[tuple[str, str, str]]:
    """Devuelve `(archivo, método, url)` por cada destino literal de las plantillas."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        src = JINJA_COMMENT.sub("", path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(TEMPLATES_DIR))
        for match in TARGET_ATTR.finditer(src):
            attr, url = match.group(1), match.group(2)
            if url in MACRO_PARAM_TARGETS:
                continue
            if attr == "action":
                method = _enclosing_form_method(src, match.start())
            else:
                method = attr.removeprefix("hx-").upper()
            found.append((rel, method, url))
    return found


def _enclosing_form_method(src: str, at: int) -> str:
    """El método de un `action` es el del `<form>` que lo contiene, no POST por defecto.

    Los filtros de pantry y de comidas/entrenamientos son formularios `method="get"`
    que empujan la query a la URL; tratarlos como POST haría fallar el test contra una
    ruta que existe.
    """
    tags = [m for m in FORM_TAG.finditer(src) if m.start() <= at]
    if not tags:
        return "POST"
    method = METHOD_ATTR.search(tags[-1].group(0))
    return method.group(1).upper() if method else "GET"


def _resolves(url: str, method: str) -> bool:
    """¿Aceptaría el router esta URL con este método?

    Se le pasa el scope al mismo `matches()` que usa Starlette en cada request. Un
    `Match.PARTIAL` es exactamente el caso que interesa detectar: el path existe pero
    el método no (un `hx-post` a una ruta que solo acepta GET), así que solo cuenta
    `Match.FULL`.
    """
    scope = {
        "type": "http",
        "method": method,
        "path": url,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    return any(route.matches(scope)[0] == Match.FULL for route in app.routes)


TEMPLATE_TARGETS = _iter_targets()


def test_the_extractor_actually_found_the_targets() -> None:
    """Si un cambio de sintaxis deja el regex en cero, los dos tests de abajo pasan
    vacíos y la red de seguridad desaparece sin que nada se ponga rojo."""
    assert len(TEMPLATE_TARGETS) > 25


@pytest.mark.parametrize(
    ("template", "method", "url"),
    TEMPLATE_TARGETS,
    ids=[f"{t}:{m}:{u}" for t, m, u in TEMPLATE_TARGETS],
)
def test_every_template_target_resolves_to_a_real_route(
    template: str, method: str, url: str
) -> None:
    resolvable = JINJA_VALUE.sub("1", url).split("?")[0]
    assert _resolves(
        resolvable, method
    ), f"{template}: {method} {url} no resuelve a ninguna ruta (se probó {resolvable!r})"


def test_no_template_target_relies_on_a_redirect() -> None:
    """La barra final importa: sin ella `redirect_slashes` responde un 307.

    Un 307 se paga entero — dos viajes por cada tecla del buscador de pantry, y HTMX
    reintenta el swap recién en la segunda respuesta. El proyecto ya tomó esta decisión
    en `home.html:196`; este test la vuelve verificable en vez de dejarla como
    comentario en una plantilla.

    La condición es la definición exacta de "depende del redirect": el destino **no**
    resuelve, y con la barra sí. Un destino que no resuelve de ninguna forma es un
    defecto distinto y lo reporta el test de arriba con su propio mensaje.
    """
    redirected = [
        (template, method, url)
        for template, method, url in TEMPLATE_TARGETS
        for resolvable in [JINJA_VALUE.sub("1", url).split("?")[0]]
        if not _resolves(resolvable, method) and _resolves(resolvable + "/", method)
    ]
    assert (
        redirected == []
    ), f"estos destinos existen solo por el redirect de la barra final: {redirected}"


# Rutas que a propósito **no** están en el smoke de `test_web_pages.py`, cada una con
# el test que sí las cubre. La lista está acá y no allá porque este test es el que
# obliga a que la suma sea completa.
SMOKE_EXCLUSIONS = {
    "/login": "test_web_auth.py — no autenticada, y con POST propio",
    "/logout": "test_web_auth.py — redirige a propósito",
    "/forgot-password": "test_web_auth.py — no autenticada, y con POST propio",
    "/reset-password/{token}": "test_web_auth.py — necesita un token propio",
    "/onboarding/": "test_web_onboarding.py — redirige según el gate",
    "/notifications/badge": "test_web_fragments.py — es un fragmento, no una página",
    "/health/{analysis_id}": "test_web_pages.py — necesita una fila propia",
    "/meals/{meal_id}": "test_web_pages.py — necesita una fila propia",
}


def test_the_page_smoke_covers_every_page_route() -> None:
    """`PAGES` es una lista escrita a mano: una página nueva no entra sola.

    Y una página que nadie rinde en los tests es exactamente la que se rompe en la
    Fase 3 sin que nada avise — es el motivo por el que existe ese smoke.
    """
    get_pages = {
        path
        for path, operations in app.openapi()["paths"].items()
        if "get" in operations and not path.startswith("/api/")
    }
    covered = set(PAGES) | set(SMOKE_EXCLUSIONS)
    assert get_pages - covered == set(), (
        "estas páginas no las rinde ningún test: "
        f"{sorted(get_pages - covered)} — agregalas a PAGES o justificalas "
        "en SMOKE_EXCLUSIONS"
    )
    assert (
        covered - get_pages == set()
    ), f"estas entradas ya no corresponden a ninguna ruta GET: {sorted(covered - get_pages)}"
