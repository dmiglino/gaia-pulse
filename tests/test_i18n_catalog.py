"""Que el catálogo `es_AR` cubra todo lo que la app muestra.

Esta es la red que faltaba, y el hueco que cierra no era una plantilla sin `_()`:
eran **llamadas correctas cuyo texto castellano nunca se escribió**. `gettext`
devuelve el `msgid` inglés cuando no encuentra entrada y no falla nada, así que una
pantalla entera en inglés se ve exactamente igual de sana que una traducida — desde
el código. Por eso se pasaron de largo tres fases: al empezar la 5.1 había **139**
`msgid` sin entrada, sobre 528 extraídos, en una app que usan dos personas que
hablan castellano.

Se mide extrayendo, nunca contra un número escrito. El plan tenía anotado "126 sin
entrada" y al re-medirlo eran 139: las fases 4.5.x habían agregado trece más. Un
número en prosa envejece en silencio; una extracción no.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = REPO_ROOT / "app" / "locales" / "es_AR" / "LC_MESSAGES" / "messages.po"

#: El mismo mapeo que `babel.cfg`, con los patrones **relativos al directorio de
#: entrada**. Escrito acá y no leído del `.cfg` a propósito: si alguien rompe el
#: mapeo del archivo, la extracción devuelve un solo `msgid` y este test pasaría
#: sin haber mirado nada. El que compara los dos es
#: `test_the_extraction_map_matches_babel_cfg`.
_METHOD_MAP = [
    ("app/**.py", "python"),
    ("app/templates/**.html", "jinja2"),
]
_OPTIONS_MAP = {"app/templates/**.html": {"extensions": "jinja2.ext.i18n"}}


def _extracted() -> list[tuple[str | None, str | tuple[str, ...]]]:
    """Los `(msgctxt, msgid)` que la app pide al catálogo, extraídos del código."""
    found = []
    for _filename, _lineno, message, _comments, context in extract_from_dir(
        str(REPO_ROOT), method_map=_METHOD_MAP, options_map=_OPTIONS_MAP
    ):
        if message:
            found.append((context, message))
    return found


@pytest.fixture(scope="module")
def extracted() -> list[tuple[str | None, str | tuple[str, ...]]]:
    return _extracted()


@pytest.fixture(scope="module")
def catalog():  # type: ignore[no-untyped-def]
    with CATALOG.open(encoding="utf-8") as f:
        return read_po(f)


def test_every_string_the_app_shows_has_a_spanish_translation(extracted, catalog) -> None:  # type: ignore[no-untyped-def]
    """El ratchet: un `msgid` nuevo sin traducir hace fallar esto, no la pantalla.

    Se chequean las dos formas de estar sin traducir, porque son dos estados
    distintos del archivo: **sin entrada** (nadie lo miró) y **con entrada vacía**
    (`msgstr ""`, que es lo que deja `pybabel update` al agregar un `msgid` nuevo, y
    que rinde el inglés igual que la ausencia).
    """
    untranslated = []
    for context, message in extracted:
        msgid = message[0] if isinstance(message, tuple) else message
        entry = catalog.get(msgid, context)
        if entry is None:
            untranslated.append((context, msgid, "sin entrada"))
            continue
        forms = entry.string if isinstance(entry.string, tuple) else (entry.string,)
        if not all(forms):
            untranslated.append((context, msgid, "traducción vacía"))

    assert not untranslated, "sin castellano:\n" + "\n".join(
        f"  [{c}] {m!r} — {why}" if c else f"  {m!r} — {why}" for c, m, why in untranslated
    )


def test_the_extraction_actually_sees_the_contextual_labels(extracted) -> None:
    """Sin esto, el test de arriba puede pasar sin haber mirado los ocho rótulos.

    Los grupos musculares van con `pgettext('muscle group', …)` porque `_('Back')` ya
    era el "Volver" de los botones de retroceso de la app. `pgettext:1c,2` está en los
    keywords por defecto de Babel — pero **un keyword que no matchea no falla**: los
    ocho simplemente no aparecerían en la extracción, y una cobertura del 100% sobre
    una lista a la que le faltan ocho no dice nada. Esto afirma que la extracción los
    ve *y* que traen su `msgctxt`, que es lo que los separa del msgid global.
    """
    contextual = {(c, m) for c, m in extracted if c}
    assert ("muscle group", "Back") in contextual, (
        "la extracción no vio los rótulos con contexto: revisar el keyword "
        "`pgettext:1c,2` contra el mapping antes de creerle a la cobertura"
    )
    assert len([1 for c, _m in contextual if c == "muscle group"]) == 8


def test_a_muscle_group_and_a_back_button_do_not_share_a_translation(catalog) -> None:  # type: ignore[no-untyped-def]
    """La colisión que originó el contexto, afirmada sobre el catálogo.

    Es el caso que ningún test podía atrapar antes: las dos entradas existen, las dos
    están traducidas, y hasta la 4.5.8 eran **la misma**.
    """
    button = catalog.get("Back")
    muscle = catalog.get("Back", "muscle group")
    assert button is not None and muscle is not None
    assert button.string and muscle.string
    assert button.string != muscle.string


def test_both_halves_of_the_mapping_contribute(extracted) -> None:  # type: ignore[no-untyped-def]
    """Que la cobertura del 100% no sea sobre media app.

    Los `msgid` salen de dos lados con dos extractores distintos: las plantillas y el
    Python. Si uno de los dos patrones deja de matchear, la extracción no falla —
    devuelve menos— y el test de cobertura pasa sobre lo que quedó. Un `msgid` conocido
    de cada lado es lo que convierte ese silencio en una falla.
    """
    plain = {m for c, m in extracted if not c}
    assert "Statistics" in plain, "no se extrajo nada de app/templates/"
    assert "Stock updated." in plain, "no se extrajo nada de app/**.py"


def test_the_extraction_map_matches_babel_cfg() -> None:
    """Que el mapeo de arriba no se separe del que usa `pybabel` a mano.

    Dos copias del mapeo es cómo el test sigue midiendo `app/templates/` después de
    que el `.cfg` empezó a mirar otra carpeta.
    """
    cfg = (REPO_ROOT / "babel.cfg").read_text(encoding="utf-8")
    for pattern, method in _METHOD_MAP:
        assert f"[{method}: {pattern}]" in cfg, f"babel.cfg no declara {method}:{pattern}"
