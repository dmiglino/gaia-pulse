---
# Generated from .agents/ by scripts/agents/sync_agent_assets.py; do not edit.
name: frontend-implementation
description: Use for Jinja2 template, HTMX partial, Alpine.js state, or Chart.js dashboard changes.
---

# Implement a frontend slice

1. Read the target route in `app/web/` and its template in
   `app/templates/`.
2. Check whether the route needs a full page and an HTMX partial, or just
   one — follow the existing sibling template's split.
3. Keep Alpine.js state local to the component; no global JS state.
4. Add or update English and Spanish strings via `app/i18n.py` /
   `app/locales/es_AR/`. **Both halves, in this commit:** the `_('…')` call *and* the
   entry in `messages.po` (by hand — `pybabel update` deletes the file's section
   comments), then `pybabel compile -d app/locales -D messages`. A msgid with no entry
   renders the English and raises nothing, so run `tests/test_i18n_catalog.py` — it is
   the only thing that sees the difference.
5. If a fragment is swapped in by HTMX and reports an outcome, give the *container*
   `role="status"` in the page and leave it there — an `aria-live` region has to exist
   in the DOM before its content changes or the screen reader announces nothing, so it
   cannot live inside the fragment being inserted.
6. Audit accessibility by reading the element, not by grepping for the attribute:
   `grep '<button' | grep -v aria-label` matches on one line and these attributes sit on
   the next, so it reports every multi-line button as a defect. An input inside a
   `<label>` with visible text needs no `for`, and a radio/checkbox group needs
   `<fieldset>` + `<legend>` to have a name at all.
7. For NLP-driven views, confirm the preview/confirm step renders before
   any save action is reachable.
8. Manually check the view at mobile width and with keyboard-only
   navigation.
