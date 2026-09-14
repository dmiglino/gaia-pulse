"""Render every macro of the v3 component library once, with its defaults.

Why this file exists: the review of the fase 2 found `components/ui.html`'s own
docstring documenting a `tabs()` signature the macro never had (`key=` instead of
a list of items). Nothing caught it because **no test rendered a single macro** —
the 18 macros were verified only by the screens that happened to call them, and 14
of the 18 had zero callers at the time they were written.

This is a smoke test, not a snapshot: it asserts the macro renders without raising
and that the one structural promise of each output holds. Comparing full HTML would
turn every visual tweak of the fase 3 into a failing test for no benefit.
"""

import re
from types import SimpleNamespace

import pytest

from app.recommendations.learning import SUBJECT_TYPES
from app.web.helpers import templates

env = templates.env
ui = env.globals["ui"]
ic = env.globals["ic"]
dm = env.globals["dm"]

# `avatar`/`attribution` leen `display_name` y `avatar_color`, no el modelo entero.
FAKE_USER = SimpleNamespace(display_name="Rocío", avatar_color="#6366f1")

TONES = ["brand", "ok", "warn", "danger", "info", "food", "move", "body", "stock"]


def test_macros_are_available_without_an_import() -> None:
    """`{% import %}` no se hereda: los macros se registran como globals del env.

    Sin esto, cada una de las 29 plantillas tiene que repetir dos líneas de import y
    una olvidada es un `UndefinedError` en tiempo de request, no en el arranque.
    """
    rendered = env.from_string("{{ ui.badge('x') }}{{ ic.icon('home') }}").render()
    assert "<span" in rendered
    assert "<svg" in rendered


# ── Los macros sin `{% call %}` ──────────────────────────────────────────────
# Cada entrada es (nombre, args, kwargs, fragmento que tiene que aparecer).
SIMPLE_MACROS = [
    ("section_title", ("Title",), {}, "<h2"),
    ("section_title", ("Title",), {"subtitle": "Sub", "icon": "home"}, "<svg"),
    ("stat", ("Weight", "72.4"), {}, "72.4"),
    ("stat", ("Weight", "72.4"), {"unit": "kg", "icon": "chart-bar", "hint": "-0.3"}, "kg"),
    ("stat", ("Weight", "72.4"), {"href": "/health"}, 'href="/health"'),
    ("badge", ("Low",), {}, "Low"),
    ("badge", ("Low",), {"tone": "neutral", "size": "sm", "icon": "warning"}, "bg-card-alt"),
    ("dot", (), {}, "aria-hidden"),
    ("btn", ("Save",), {}, "<button"),
    ("btn", ("Save",), {"href": "/", "icon": "check", "icon_right": "chevron-right"}, "<a "),
    ("btn", (), {"size": "icon", "icon": "x", "aria_label": "Close"}, 'aria-label="Close"'),
    ("btn", ("Delete",), {"tone": "danger", "block": True, "type": "button"}, 'type="button"'),
    ("avatar", (FAKE_USER,), {}, "#6366f1"),
    ("avatar", (FAKE_USER,), {"size": "lg", "ring": True}, "ring-2"),
    ("attribution", (FAKE_USER,), {}, "Rocío"),
    ("notice", ("Heads up",), {}, 'role="note"'),
    ("notice", ("Boom",), {"tone": "danger", "title": "Error"}, 'role="alert"'),
    ("progress", (40,), {}, 'aria-valuenow="40"'),
    ("progress", (None,), {}, 'aria-valuenow="0"'),
    ("progress", (999,), {"max": 100}, 'aria-valuenow="100"'),
    ("progress", (10,), {"max": 0}, 'aria-valuenow="0"'),
    ("field", ("Weight", "weight"), {}, 'name="weight"'),
    ("field", ("Weight", "weight"), {"unit": "kg", "hint": "In kg", "required": True}, "pr-12"),
    ("empty_state", ("Nothing here",), {}, "Nothing here"),
    ("empty_state", ("Nothing",), {"action_label": "Add", "action_href": "/x"}, 'href="/x"'),
    ("theme_toggle", (), {}, "data-theme-toggle"),
]


@pytest.mark.parametrize(("name", "args", "kwargs", "expected"), SIMPLE_MACROS)
def test_macro_renders(name: str, args: tuple, kwargs: dict, expected: str) -> None:
    out = str(getattr(ui, name)(*args, **kwargs))
    assert expected in out, out


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("card", ""),
        ("card_link", "'/x'"),
        ("page_header", "'Title'"),
        ("field_wrap", "'Label', 'f-x'"),
        # Los tres con `caller` opcional: la rama *con* cuerpo es la que se rompe si
        # alguien cambia `caller is defined` por `caller`.
        ("notice", "'Heads up'"),
        ("empty_state", "'Nothing here'"),
    ],
)
def test_call_macros_render_their_body(name: str, args: str) -> None:
    """Los macros que se usan con `{% call %}` y por eso no se pueden invocar como
    función: `card()` sin un `caller` explota."""
    out = env.from_string(f"{{% call ui.{name}({args}) %}}<p>INSIDE</p>{{% endcall %}}").render()
    assert "<p>INSIDE</p>" in out, out


def test_tabs_renders_navigation_and_marks_the_current_one() -> None:
    """El defecto que motivó este archivo: el docstring documentaba `tabs(key=)`.

    Y son navegación, no un widget de pestañas: `aria-current="page"`, no
    `role="tab"` — ese rol promete `aria-controls` a un `role="tabpanel"`, roving
    `tabindex` y navegación por flechas, nada de lo cual existe acá.
    """
    out = str(
        ui.tabs(
            [
                {"key": "meals", "label": "Meals", "href": "/history?tab=meals"},
                {"key": "workouts", "label": "Workouts", "href": "/history", "icon": "bolt"},
                {"key": "all", "label": "All", "href": "/history?tab=all", "count": 12},
            ],
            current="meals",
            label="History sections",
        )
    )
    assert 'aria-label="History sections"' in out
    assert out.count('aria-current="page"') == 1
    assert 'role="tab' not in out
    assert ">12<" in out


@pytest.mark.parametrize("tone", TONES)
def test_every_tone_is_a_real_token_family(tone: str) -> None:
    """Los macros con `tone` arman las clases por concatenación (`'bg-' ~ tone ~
    '-soft'`), así que un tono cuya familia no exista rinde una clase que Tailwind no
    genera: el elemento sale transparente y **sin ningún error**. Este test ata los
    tonos que los macros aceptan a los tokens que `app.css` define de verdad.
    """
    out = str(ui.badge("x", tone=tone)) + str(ui.notice("x", tone=tone)) + str(ui.dot(tone=tone))
    assert f"bg-{tone}-soft" in out
    assert f"text-{tone}-ink" in out
    assert f"bg-{tone}-solid" in out

    with open("app/static/css/app.css", encoding="utf-8") as fh:
        css = fh.read()
    for slot in ("soft", "ink", "solid"):
        assert f"--{tone}-{slot}:" in css, f"falta --{tone}-{slot} en app.css"


@pytest.mark.parametrize("subject_type", sorted(SUBJECT_TYPES))
def test_every_subject_type_the_engine_learns_has_a_label_and_an_icon(subject_type: str) -> None:
    """Los cinco tipos de sujeto tienen que tener rótulo e ícono propios, no el de reserva.

    Los dos macros tienen un fallback —`| title` para el rótulo, `sparkles` para el ícono—
    y esos fallbacks son para datos viejos, no para los tipos que el motor aprende hoy. Sin
    este test, agregar un `SUBJECT_TYPES` nuevo saca un grupo titulado "Muscle Group" en
    inglés dentro de una app en castellano, y nada falla.
    """
    label = str(dm.subject_type_label(subject_type))
    assert label
    assert label != subject_type.replace("_", " ").title(), "cayó en el rótulo de reserva"

    icon = str(dm.subject_type_icon(subject_type))
    assert "<svg" in icon
    assert str(ic.icon("sparkles")) not in icon, "cayó en el ícono de reserva"
    #: Y el tono tiene que ser una familia real: el macro arma `bg-{{ tone }}-soft` por
    #: concatenación, así que un tono inventado rinde una clase que Tailwind no genera y el
    #: ícono sale transparente **sin ningún error** (mismo riesgo que `test_every_tone_*`).
    assert dm.SUBJECT_TYPE_TONES[subject_type] in TONES


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("toward", "bg-ok-soft"),
        ("away", "bg-danger-soft"),
        ("mixed", "bg-card-alt"),
    ],
)
def test_each_direction_band_gets_its_own_badge(band: str, expected: str) -> None:
    """Las tres palabras que `SubjectAffinity.direction_band` puede devolver, y sus tonos."""
    assert expected in str(dm.learned_direction_badge(band))


@pytest.mark.parametrize("band", ["plenty", "some", "new"])
def test_each_confidence_band_gets_its_own_words(band: str) -> None:
    assert str(dm.learned_confidence_label(band)).strip()


def test_an_unknown_band_renders_nothing_instead_of_the_wrong_label() -> None:
    """Los cortes viven en `SubjectAffinity`, y los macros solo mapean palabra → msgid.

    Si mañana aparece una cuarta banda, el hueco se nota mirando la pantalla; un `{% else %}`
    que la rotulara con la etiqueta más cercana la mostraría mal y en silencio, que es
    exactamente el modo de falla que sacó los cortes de la plantilla.
    """
    assert str(dm.learned_direction_badge("sideways")).strip() == ""
    assert str(dm.learned_confidence_label("certain")).strip() == ""


@pytest.mark.parametrize(
    ("days", "expected"),
    [(None, ""), (0, None), (1, None), (5, "5")],
)
def test_the_recency_label_says_the_first_two_days_in_words(days, expected) -> None:
    """Decir "hace 0 días" no es una frase, y `None` —una señal sin `created_at`— no rinde nada:
    una fecha inventada sería peor que un hueco."""
    out = str(dm.learned_recency_label(days)).strip()
    if expected == "":
        assert out == ""
    elif expected is None:
        assert out and "0" not in out and "1" not in out
    else:
        assert expected in out


def test_unknown_icon_falls_back_to_a_visible_glyph() -> None:
    """Un nombre mal escrito tiene que verse, no desaparecer: un `<svg>` vacío es un
    hueco que nadie nota hasta que un usuario lo reporta."""
    assert str(ic.icon("no-such-icon")) == str(ic.icon("question"))
    assert 'd=""' not in str(ic.icon("no-such-icon"))


def test_every_icon_name_renders_a_path() -> None:
    """Los 40-y-pico íconos del diccionario, uno por uno: la v1 tenía copias
    truncadas del mismo SVG (el rayo sin la mitad inferior) porque la única forma de
    reusar un ícono era pegarlo."""
    # Los nombres salen del fuente y no del módulo: Jinja no exporta las variables
    # que arrancan con `_`, y `_STROKE` es privado a propósito (nadie fuera del
    # macro `icon` debería leer el diccionario).
    source = env.loader.get_source(env, "components/icons.html")[0]  # type: ignore[union-attr]
    names = re.findall(r"^\s{2}'([a-z-]+)':", source, re.MULTILINE)
    assert len(names) > 40
    for name in names:
        out = str(ic.icon(name))
        assert 'd="M' in out, f"{name}: {out}"
    assert "<circle" in str(ic.spinner())


def test_icons_are_hidden_from_screen_readers() -> None:
    """Todo ícono es decorativo: el nombre accesible lo pone el `aria_label` del
    botón o el texto de al lado. Un `<svg>` sin `aria-hidden` se anuncia como
    "imagen" en medio de la frase."""
    assert 'aria-hidden="true"' in str(ic.icon("home"))
    assert 'aria-hidden="true"' in str(ic.spinner())
