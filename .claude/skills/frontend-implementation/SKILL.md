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
   `app/locales/es_AR/`.
5. For NLP-driven views, confirm the preview/confirm step renders before
   any save action is reachable.
6. Manually check the view at mobile width and with keyboard-only
   navigation.
