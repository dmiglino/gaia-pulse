# Agent instructions — repository index

This file is the canonical entry point for any agent (human-directed or
autonomous) working in GaiaPulse. Tool-specific files (`CLAUDE.md`, etc.)
point back here instead of duplicating it.

## Source of truth

- Stack, schema, project structure, environment variables, NLP pipeline,
  recommendation engine, and known tradeoffs: `README.md`.
- Environment variables and their shape (never real secrets): `.env.example`.
- The agent team and their responsibilities: `.agents/agents/*.md`.
- Task playbooks the agents follow: `.agents/skills/*/SKILL.md`.
- `.claude/agents/` and `.claude/skills/` are generated from `.agents/` by
  `scripts/agents/sync_agent_assets.py` — edit `.agents/`, never `.claude/`.

## Non-negotiable rules

1. Every HTTP route lives under either `/api/...` (JSON, FastAPI dependency
   returns 401 on auth failure) or a page route (SSR, redirects to `/login`
   on auth failure). Never mix the two response styles on one route.
2. Layering is one-directional: `api/` and `web/` call `services/`,
   `services/` call `repositories/`, only `repositories/` touch
   SQLAlchemy models. No layer reaches past the one directly below it.

   **This one is the target, not a description of the tree today**, and
   saying so is the point: a rule the code contradicts in eighteen places
   gets obeyed halfway and stops nothing. Measured on the `v3` branch:
   `app/api/`, `app/web/`, `app/services/` **and `app/recommendations/`** are
   clean of `.query(` (3 of 20 `web/` modules and 10 of 12 `services/` modules
   still import `app.models`, and 9 of the 11 in `app/recommendations/` do too —
   that import is expected there for type annotations and is not the violation
   being counted).

   So the rule is a **ratchet**, and that half is non-negotiable: new code
   adds no query outside `repositories/`, and a change that touches a module
   holding inline queries takes *its* queries down to a repository as part of
   the change (`app/recommendations/engine.py` went 15 → 14 → 0 that way, y la
   4.5.1 bajó con él los 2 de `meal_generator`, los 2 de `activity_generator` y
   los 3 de `blood_analysis_service.py`; los últimos 5, en
   `generators/pantry_generator.py`, cayeron con la 4.5.7 — y tres de ellos
   sin agregar método nuevo, porque el nombre que iban a buscar de a uno ya
   venía en el `joinedload` de la consulta que el generador ya hacía).
   Reviewers block on the ratchet, not on the backlog: the remaining backlog is
   the `app.models` imports above, and those are the expected kind.

   **`app/jobs/` is a fourth entry point, and it calls `repositories/`
   directly on purpose.** A job has no request and no acting user: it is the
   clock calling in, so there is no service whose job it is to hold the
   session. It opens a `SessionLocal()`, reads through repositories, and is
   bound by the same ratchet as everything above — a `select(User)` written
   inline inside a job is the violation the rule is about, and the fix is a
   repository method, not a helper in the job module. Business logic that a
   route would also need belongs in a service the job calls; the job's own
   body is allowed to be a loop over people plus the decision of *when*.
3. No NLP-extracted or LLM-extracted data reaches a domain table before a
   human confirms it. Every ingestion path goes through the
   `pending_confirmation` state on `NLPIngestionEvent`.
4. Every query against a personal-data table (`body_metric_logs`,
   `meal_items_consumed`, `workout_exercises`, `recommendation_preferences`,
   `behavior_signals`, blood-analysis tables) filters by the acting user's
   `user_id`, even when the acting user is in the same household as the data
   owner. Learning is per person: `learning.record_signal()` takes a
   `user_id` and never a `household_id`.
5. PostgreSQL is the system of record; SQLite (in-memory) exists only for
   test speed. Any feature that behaves differently between the two engines
   is a bug, not a documented tradeoff, unless README.md already says
   otherwise.
6. Do not build for a hypothetical third household member, a second LLM
   provider, or a mobile client unless the user asks for it. GaiaPulse is a
   two-person household tool — match the code's ambition to that scope.

## Required checks before completion

Python tooling lives in the local venv — invoke it as
`.venv/bin/python -m <tool>`. Bare `ruff` is not on `PATH` at all, and the
`pytest`/`black`/`mypy` that are belong to a different interpreter, so a bare
invocation either fails outright or reports numbers from the wrong
environment.

- `.venv/bin/python -m pytest tests/` (and
  `--cov=app --cov-report=term-missing` when touching NLP or
  recommendations)
- `.venv/bin/python -m ruff check .`
- `.venv/bin/python -m black --check .`
- `.venv/bin/python -m mypy app`
- `alembic check` when models or migrations changed — it needs PostgreSQL and
  **does not run on this machine** (nothing listens on `localhost:5432`). Run
  it under `docker compose`, or report explicitly that it could not run
  instead of skipping it in silence — see "Verificación" in
  `docs/v3-plan.md`, which also holds the measured pre-existing
  lint/type/test baseline.
- No secret values committed; `.env.example` updated if env vars changed
- `README.md` updated if the change alters documented stack, schema,
  structure, NLP behavior, recommendation behavior, or tradeoffs
- `docs/gaiapulse-v3.md` updated on the same trigger, and specifically:
  its "lo que quedó afuera" section when a change **closes** one of those
  gaps, and its measured-numbers table when tables, routes, templates,
  macros, catalog entries or test counts move. That table exists because
  `README.md` once carried numbers nobody re-measured; it earns its keep
  only if it is re-measured, and it lists the commands to do it.
- `docs/guia-de-uso.md` updated when a change alters **what a phrase does**
  or what a button does — it names screen labels and asserts parser
  behavior phrase by phrase, so a parser or template change can falsify it
  silently. Its claims are verifiable: re-run the phrases, don't reason
  about them.
- `python3 scripts/agents/sync_agent_assets.py --check` if anything under
  `.agents/` changed
- A `security-privacy` review for anything touching auth, sessions,
  personal health data, secrets, or file uploads (voice/blood-analysis)

## Change discipline

- Make the smallest change that correctly solves the request. Do not
  bundle unrelated refactors, renames, or "while I'm here" cleanups.
- Never commit, push, or tag on your own initiative — the user does this
  manually unless they explicitly ask you to.
- For a change that spans more than one layer (e.g. a new feature touching
  backend + frontend + persistence), use the `integrator` agent to sequence
  the work and confirm the full gate before calling it done.

## Agent routing

| Agent | Owns |
|---|---|
| `backend` | FastAPI routers (`api/`, `web/`), `services/`, request/response schemas, `app/jobs/` and `app/core/clock.py` |
| `nlp-recommendations` | `app/nlp/`, `app/recommendations/`, confirmation gate, feedback loop — **including any job that writes a `BehaviorSignal`**, wherever it lives |
| `frontend` | Jinja2 templates, HTMX partials, Alpine state, Tailwind, Chart.js, i18n strings |
| `data-persistence` | SQLAlchemy models, Alembic migrations, `repositories/`, household/user isolation |
| `security-privacy` | Read-only review: auth, sessions, secrets, personal-data isolation, uploads |
| `code-health-qa` | Lint/type/test gate, duplication/dead-code scan, functional test-case coverage |
| `documentation-steward` | `README.md`, `.env.example` accuracy |
| `instruction-steward` | This file, `CLAUDE.md`, `docs/`, `.agents/` skills and agent definitions |
| `integrator` | Sequences multi-agent changes, runs the full gate, calls final verdict |

A background job usually has **two** owners and needs both: `backend` for the
registration and the session, `nlp-recommendations` for what it writes. The
absence sweep is the worked example — a job whose entire purpose is to write a
learning signal.

Edit `.agents/agents/*.md` or `.agents/skills/*/SKILL.md` first when the
team itself needs to change, then run
`python3 scripts/agents/sync_agent_assets.py --write` before finishing.
