---
name: data-persistence-review
description: Use for SQLAlchemy model, Alembic migration, or repository query changes — especially anything touching household/user isolation.
---

# Review a persistence change

1. Read the "Database schema" and "Shared / Household Data Design"
   sections of `README.md`.
2. For a new/changed column: decide column vs. JSONB using the "stable
   concept goes in a column" rule.
3. For a new table or column: add a new Alembic revision — never edit
   `0001_initial_schema.py`. `--autogenerate` compares against a live
   PostgreSQL, which is not running on this machine, so write the revision by
   hand following `0003_suggestion_subject.py` (explicit `revision` /
   `down_revision`, `upgrade()` and `downgrade()`) rather than reporting that
   the command failed.
4. For a query touching per-user data: confirm it filters by the
   participant/user row, not the household-scoped parent alone.
5. Run `.venv/bin/python -m pytest tests/` (SQLite in-memory). `alembic
   upgrade head` and `alembic check` need PostgreSQL — they go through
   `docker compose up`, and if you can't run them, say so instead of implying
   parity was verified.
