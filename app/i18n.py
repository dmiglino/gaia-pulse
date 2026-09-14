"""Internationalization (i18n) support for GaiaPulse.

Usage in Python code:
    from app.i18n import _
    raise ValueError(_("Invalid email or password."))

Usage in Jinja2 templates (automatic after setup_jinja2_i18n is called):
    {{ _('Key text') }}
    {{ _('Hello, %(name)s!') % {'name': user.display_name} }}
    {% trans count=items|length %}%(count)d item{% pluralize %}%(count)d items{% endtrans %}

A missing translation is invisible: gettext returns the English msgid when it finds
no entry, and nothing raises. That is how three phases of v3 shipped 139 untranslated
strings to a Spanish-speaking household. `tests/test_i18n_catalog.py` is the ratchet —
it extracts on every run and fails on the first msgid with no Spanish, so the gap
cannot come back silently. Run it after adding any user-facing string.

Workflow for adding/updating translations:
    pybabel extract -F babel.cfg -o /tmp/gp.pot --no-location --sort-output .
    # then edit app/locales/es_AR/LC_MESSAGES/messages.po BY HAND, appending the new
    # entries under the right `# ─── section ───` comment.
    pybabel compile -d app/locales -D messages --statistics
    # or just restart the app — _ensure_mo_compiled auto-compiles when the .mo is
    # missing or older than the .po

Deliberately NOT `pybabel update`: it rewrites the .po from the .pot, which drops the
section comments and the notes recording why a short string got the translation it did
("Out" is a meal context, not an exit). Those comments are the only documentation the
catalog has, so the file is maintained by hand and the .pot is a scratch file — hence
/tmp and not a tracked path. Both the .po and the compiled .mo are committed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from babel.support import Translations

if TYPE_CHECKING:
    from jinja2 import Environment

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LOCALE = "es_AR"


def _ensure_mo_compiled(locale: str) -> None:
    """Auto-compile .po → .mo when the binary is missing or older than the source."""
    po_path = LOCALES_DIR / locale / "LC_MESSAGES" / "messages.po"
    mo_path = LOCALES_DIR / locale / "LC_MESSAGES" / "messages.mo"

    if not po_path.exists():
        return

    needs_compile = (
        not mo_path.exists()
        or mo_path.stat().st_mtime < po_path.stat().st_mtime
    )
    if not needs_compile:
        return

    try:
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po

        with po_path.open("rb") as f:
            catalog = read_po(f)
        mo_path.parent.mkdir(parents=True, exist_ok=True)
        with mo_path.open("wb") as f:
            write_mo(f, catalog)
        logger.debug("Compiled translations: %s → %s", po_path.name, mo_path.name)
    except Exception:
        logger.exception("Failed to compile translations for locale '%s'", locale)


def get_translations(locale: str = DEFAULT_LOCALE) -> Translations:
    """Load (auto-compiling if necessary) gettext Translations for *locale*."""
    _ensure_mo_compiled(locale)
    return Translations.load(
        dirname=str(LOCALES_DIR),
        locales=[locale],
        domain="messages",
    )


# ── Module-level translator for use in Python application code ──────────────

_translations: Translations | None = None


def _get_translations() -> Translations:
    global _translations
    if _translations is None:
        from app.core.config import get_settings

        _translations = get_translations(get_settings().default_locale)
    return _translations


def _(text: str, **kwargs: Any) -> str:
    """Return the translation of *text* in the configured default locale.

    Supports %-style keyword substitution::

        _("Hello, %(name)s!", name="Diego")  →  "¡Hola, Diego!"
    """
    translated = _get_translations().ugettext(text)
    return translated % kwargs if kwargs else translated


# ── Jinja2 integration ───────────────────────────────────────────────────────


def setup_jinja2_i18n(env: "Environment", locale: str = DEFAULT_LOCALE) -> None:
    """Install gettext translations into a Jinja2 Environment.

    Enables in every template:
    - ``{{ _('key') }}``
    - ``{{ ngettext('singular', 'plural', count) }}``
    - ``{% trans %}...{% endtrans %}``
    """
    env.add_extension("jinja2.ext.i18n")
    translations = get_translations(locale)
    env.install_gettext_translations(translations, newstyle=True)
    logger.debug("Jinja2 i18n configured for locale '%s'", locale)
