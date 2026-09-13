---
# Generated from .agents/ by scripts/agents/sync_agent_assets.py; do not edit.
name: backend-implementation
description: Use for FastAPI endpoint, service, or repository changes in GaiaPulse's dual-router backend.
---

# Implement a backend slice

1. Read the affected `app/api/` or `app/web/` router, its service in
   `app/services/`, and its repository in `app/repositories/`.
2. Add or adjust the Pydantic schema in `app/schemas/` first — it's the
   contract.
3. Implement the service function (business logic), then the repository
   query (DB access) it calls.
4. Wire the router to the service only; no DB access or business logic in
   the router itself.
5. Add a focused pytest case in the matching `tests/test_*.py` file.
6. Run `.venv/bin/python -m pytest tests/ -k <area>`,
   `.venv/bin/python -m ruff check app/`, `.venv/bin/python -m mypy app/`.
