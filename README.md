# GaiaPulse

A shared household wellness app for Diego and Rocío. GaiaPulse combines food intake tracking, pantry management, workout logging, and body metrics into a single app built for two people who live together — with per-user data isolation, natural language input, an intelligent recommendation engine, and a server-rendered UI that works without a heavy frontend build step.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Setup — Docker / Podman](#setup--docker--podman)
- [Setup — Local Development](#setup--local-development)
- [Environment Variables](#environment-variables)
- [NLP Design](#nlp-design)
- [Recommendation Engine](#recommendation-engine)
- [Shared / Household Data Design](#shared--household-data-design)
- [Testing](#testing)
- [Known Tradeoffs & Roadmap](#known-tradeoffs--roadmap)

---

## Overview

GaiaPulse is built around the concept of a **household** — a single shared unit that Diego and Rocío both belong to. The pantry, meal events, and workout sessions exist at the household level, but each person's consumption records, body metrics, and preferences are fully isolated per user.

The app accepts natural language and voice input for all data entry and requires explicit confirmation before saving anything — the NLP layer surfaces a parsed preview, the user reviews it, and only then does data get written.

---

## Features

- **Food intake tracking** — log meals with per-person item attribution (e.g. "Rocío had salad, Diego had rice and chicken")
- **Pantry / stock management** — shared household inventory with stock add/consume events and low-stock alerts
- **Workout logging** — session-level tracking with per-exercise sets, reps, load, distance, and duration
- **Body metrics** — weight, body fat %, muscle mass, waist circumference, and sleep hours per user
- **Natural language input** — type or speak in English or Spanish; a two-layer NLP pipeline parses the intent
- **Voice input** — optional speech-to-text (Whisper) feeds the same NLP pipeline
- **Preview / confirm flow** — NLP results are stored as `pending_confirmation` and shown to the user before any data is saved
- **Recommendation engine** — meal, activity, and pantry suggestions personalised per user and updated by behavior signals
- **Dashboard** — Chart.js visualisations: weight trend, workout frequency, muscle group distribution, meal type breakdown, activity calendar
- **Background jobs** — APScheduler runs four jobs at fixed local wall-clock times in `TIMEZONE`, not on intervals, so a restart never moves them (see [Background job schedule](#background-job-schedule)). The three notification jobs skip their run entirely inside the quiet window (`QUIET_HOURS_START`–`QUIET_HOURS_END`); suggestion generation is not gated by it
- **PWA manifest** — installable on mobile home screens
- **OpenAPI docs** — available at `/api/docs` and `/api/redoc`

---

## Architecture

### Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.115 (dual routing: server-rendered + REST) |
| Templating | Jinja2 3.x |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Primary database | PostgreSQL 16 (JSONB columns for user prefs, macros, metadata) |
| Test database | SQLite in-memory |
| Frontend | HTMX 2.x, Alpine.js 3.x, Tailwind CSS (Play CDN), Chart.js |
| Background jobs | APScheduler 3.x |
| Auth | `itsdangerous` URLSafeTimedSerializer session tokens, `passlib[bcrypt]` |
| NLP Layer 1 | Deterministic regex + keyword rules (`app/nlp/rules.py`) |
| NLP Layer 2 | OpenAI function-calling adapter (`app/nlp/adapters/openai_adapter.py`) |
| NLP orchestrator | `NLPParser` in `app/nlp/parser.py` |
| Recommendation engine | `app/recommendations/` — filters → generators → scorer |
| Python version | 3.12+ |

### Request routing

FastAPI mounts two routers:

- `/api/...` — JSON REST endpoints; unauthenticated requests get a 401 JSON response
- All other paths — Jinja2 server-rendered HTML; unauthenticated requests redirect to `/login`

HTMX drives partial-page updates on the rendered views. Alpine.js handles lightweight client-side state (modals, toggles). There is no separate frontend build process.

### Background job schedule

Jobs run at fixed local wall-clock times in `TIMEZONE` (APScheduler `CronTrigger`), not
on an interval, so the times do not shift when the process restarts. The table of record
is `_SCHEDULE` in `app/jobs/scheduler.py`.

| Job | Local time | Why then |
|---|---|---|
| `metric_reminders` | 08:20 | Before breakfast — weigh-ins are done fasted |
| `low_stock_notifications` | 09:10 | Morning, while there is still time to shop |
| `inactivity_notifications` | 13:05 | Midday, with the day still ahead |
| `suggestion_generation` | 07:40, 18:40 | Before breakfast, and before dinner is decided |

The first three create notifications and are gated by the quiet window: inside it they
skip the run entirely rather than deferring it, since what they would announce is still
true tomorrow. `suggestion_generation` only writes suggestions, which nobody is woken up
for, so it is not gated. `ENABLE_BACKGROUND_JOBS=false` registers none of them.

### Database schema

18 tables across one Alembic migration (`0001_initial_schema`):

| Table | Purpose |
|---|---|
| `households` | Single shared unit; holds timezone and settings |
| `users` | Per-user profiles, goals, dietary preferences (JSONB) |
| `body_metric_logs` | Weight, body fat, waist, sleep per user |
| `food_items` | Canonical food catalogue with macros (JSONB) |
| `pantry_stock` | Current household inventory per food item |
| `pantry_movements` | Immutable add/consume ledger |
| `meal_events` | Shared meal event (household-scoped) |
| `meal_participants` | Bridge: which users ate at a meal event |
| `meal_items_consumed` | Per-participant food items with quantities |
| `recipes` | Household recipe book |
| `exercise_types` | Canonical exercise catalogue |
| `workout_sessions` | Shared workout session (household-scoped) |
| `workout_participants` | Bridge: which users participated |
| `workout_exercises` | Per-participant exercise sets/reps/load |
| `suggestions` | Recommendation records with status lifecycle |
| `recommendation_preferences` | Explicit like/dislike/avoid/impossible signals |
| `nlp_ingestion_events` | NLP parse results pending or confirmed |
| `notifications` | In-app notification inbox |
| `behavior_signals` | Implicit and explicit learning signals |

---

## Project Structure

```
GaiaPulse/
├── app/
│   ├── main.py                  # App factory, lifespan, exception handlers
│   ├── core/
│   │   ├── clock.py             # Local "now"/"today", day bounds, quiet hours
│   │   └── config.py            # Pydantic Settings (env-driven)
│   ├── models/                  # SQLAlchemy ORM models (one file per domain)
│   ├── schemas/                 # Pydantic request/response schemas
│   ├── repositories/            # DB query layer (one per model group)
│   ├── services/                # Business logic (meal, workout, pantry, etc.)
│   ├── api/                     # REST JSON routers
│   ├── web/                     # Server-rendered Jinja2 routers
│   ├── nlp/
│   │   ├── rules.py             # Layer 1: regex + keyword parser
│   │   ├── parser.py            # NLPParser orchestrator
│   │   ├── intents.py           # Pydantic intent models
│   │   └── adapters/
│   │       └── openai_adapter.py  # Layer 2: OpenAI function-calling
│   ├── recommendations/
│   │   ├── engine.py            # RecommendationEngine (main entry point)
│   │   ├── filters.py           # Hard constraint filtering
│   │   ├── scorer.py            # Behavior signal scoring + diversity penalty
│   │   └── generators/          # meal_generator, activity_generator, pantry_generator
│   ├── jobs/
│   │   ├── scheduler.py         # APScheduler cron schedule (local times)
│   │   ├── notification_jobs.py
│   │   └── suggestion_jobs.py
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS, JS, PWA manifest, icons
├── alembic/
│   └── versions/
│       └── 0001_initial_schema.py
├── tests/                       # pytest test suite
├── seed.py                      # Creates household, users, and sample data
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

---

## Setup — Docker / Podman

The simplest path. Requires Docker Compose v2 or Podman Compose.

```bash
# Clone the repo, then:
podman-compose up --build
# or
docker-compose up --build
```

On startup the container runs:

1. `alembic upgrade head` — applies all migrations
2. `python seed.py` — creates the household, both users, and sample data
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

App is available at **http://localhost:8000**.

### Run DB and app separately (Docker DB + Podman app)

If you want to keep PostgreSQL running while restarting only the app:

```bash
# 1) Start DB once with Docker
docker run -d \
  --name gaiapulse-db \
  -e POSTGRES_DB=gaiapulse \
  -e POSTGRES_USER=gaiapulse \
  -e POSTGRES_PASSWORD=gaiapulse \
  -p 5432:5432 \
  -v gaiapulse_pgdata:/var/lib/postgresql/data \
  postgres:16-alpine

# 2) Start only app with Podman Compose
podman-compose -f podman-compose.app.yml up --build
```

Notes:
- `podman-compose.app.yml` points to `host.containers.internal:5432` by default.
- Override DB URL if needed: `DATABASE_URL=postgresql://... podman-compose -f podman-compose.app.yml up`.
- Seed data manually when needed (it is not auto-run in this mode):
  `podman-compose -f podman-compose.app.yml run --rm app python seed.py`.

### Default credentials

| User | Email | Password |
|---|---|---|
| Diego | diego@gaiapulse.app | diego123 |
| Rocío | rocio@gaiapulse.app | rocio123 |

---

## Setup — Local Development

Requires Python 3.12+ and a running PostgreSQL 16 instance.

```bash
# 1. Install the package with dev dependencies
python3 -m pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env
# Edit .env — at minimum set DATABASE_URL

# 3. Run migrations
alembic upgrade head

# 4. Seed the database
python3 seed.py

# 5. Start the dev server
uvicorn app.main:app --reload
```

App is available at **http://localhost:8000**.

---

## Environment Variables

All variables can be set in `.env` (see `.env.example`) or passed directly to the container.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://gaiapulse:gaiapulse@localhost:5432/gaiapulse` | PostgreSQL connection string |
| `APP_SECRET_KEY` | Yes | `dev-secret-key-change-in-production` | Session token signing key — change this in production |
| `APP_ENV` | No | `development` | `development`, `staging`, or `production` |
| `APP_DEBUG` | No | `true` | Enables uvicorn reload and DEBUG logging |
| `OPENAI_API_KEY` | No | _(empty)_ | Enables NLP Layer 2 and voice input. Without it, only the rule-based Layer 1 parser runs |
| `STT_PROVIDER` | No | `none` | Set to `whisper` to enable speech-to-text |
| `STT_API_KEY` | No | _(empty)_ | STT API key; falls back to `OPENAI_API_KEY` if not set |
| `ENABLE_BACKGROUND_JOBS` | No | `true` | Registers the four APScheduler jobs (see [Background job schedule](#background-job-schedule)) |
| `QUIET_HOURS_START` | No | `22` | Whole local hour (0–23) the quiet window opens. Notification jobs scheduled inside it skip their run entirely — they are not deferred |
| `QUIET_HOURS_END` | No | `8` | Whole local hour (0–23) the window closes. The window wraps midnight when start > end (the default 22 → 8); set it equal to `QUIET_HOURS_START` to disable quiet hours. A value outside 0–23 fails startup |
| `TIMEZONE` | No | `America/Argentina/Buenos_Aires` | Household timezone. Defines job run times, quiet hours, and every "today" the app shows (Home counters, `/meals` and `/workouts` day filters, dashboard calendar). An unknown name falls back to UTC with a logged warning |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Model used by the OpenAI adapter |

---

## NLP Design

All data entry in GaiaPulse flows through a two-layer NLP pipeline. The result is always surfaced as a preview that the user must confirm — data is **never auto-saved** from NLP input.

### Layer 1 — Rule-based (`app/nlp/rules.py`)

The rule parser runs on every request, fully offline and synchronously. It handles the ~80% common case using:

- **Trigger pattern matching** — regex patterns detect intent type (meal, workout, body metric, stock add/consume, preference update)
- **Participant resolution** — detects mentions of "Diego", "Rocío", "we", "both", or first-person pronouns to assign items to the right user(s)
- **Per-user food splitting** — parses sentences like "Rocío ate salad, Diego had rice" into separate per-user item lists
- **Quantity/unit extraction** — handles numeric and word quantities (English and Spanish) with unit normalisation (`grams → g`, `tablespoons → tbsp`, etc.)
- **Meal type and time inference** — maps keywords and time phrases (`this morning → breakfast`, `tonight → dinner`) in English and Spanish
- **Exercise extraction** — matches ~40 exercise keywords to canonical names and muscle groups
- **Duration parsing** — extracts hours/minutes from phrases like "45 minutes" or "1 hour"
- **Body metric extraction** — parses weight (with kg/lb conversion), body fat %, waist cm, and sleep hours
- **Preference parsing** — detects like/dislike/avoid/impossible signals for foods and activities

The result is a `ParseResult` containing one or more typed intent objects (`MealIntent`, `WorkoutIntent`, `BodyMetricIntent`, `StockAddIntent`, `StockConsumeIntent`, `PreferenceIntent`) and an `overall_confidence` score.

### Layer 2 — LLM adapter (`app/nlp/adapters/openai_adapter.py`)

The OpenAI adapter is invoked only when all three conditions are true:

1. `OPENAI_API_KEY` is set (`settings.nlp_enabled` is `True`)
2. `use_llm=True` on the call
3. Layer 1's `overall_confidence` is below **0.7**

The adapter calls the OpenAI API using function-calling with a strict JSON schema (`parse_wellness_input`), passing the original text plus the Layer 1 result as context. If the LLM response has equal or higher confidence than Layer 1, it replaces it; otherwise Layer 1 is kept. The adapter fails gracefully — any network error, quota issue, or malformed response falls back to the Layer 1 result.

### Orchestration (`app/nlp/parser.py`)

`NLPParser.parse()` coordinates both layers. The `parser_layer` field on the returned `ParseResult` records which layer produced the final result (`"rules"`, `"llm"`, or `"combined"`).

### Confirmation flow

When the user submits natural language input:

1. `NLPParser.parse()` runs and produces a `ParseResult`
2. An `NLPIngestionEvent` row is created with `status="pending_confirmation"` and the parsed intents stored as JSONB
3. The UI (via HTMX) renders a preview card showing what was understood
4. The user confirms or edits the preview
5. On confirmation the actual domain records (meal, workout, metric, etc.) are created and the event status is updated to `"confirmed"`

---

## Recommendation Engine

The engine lives in `app/recommendations/` and is composed of four stages.

### Stage 1 — Candidate generation

Three generators produce raw suggestion dicts:

- **`meal_generator`** — suggests meals based on recent eating patterns, pantry availability, and nutritional goals
- **`activity_generator`** — suggests workouts based on workout history, preferred activities, and recovery signals
- **`pantry_generator`** — identifies low-stock items and shopping recommendations at the household level

### Stage 2 — Hard constraint filtering (`filters.py`)

Candidates that violate explicit user constraints are removed entirely before scoring:

- Foods in `disliked_foods_json`, `dietary_restrictions_json`, or with a `dislikes`/`impossible` `RecommendationPreference`
- Activities in `impossible_activities_json` or `disliked_activities_json`, or with a matching negative preference

### Stage 3 — Behavior signal scoring (`scorer.py`)

Each surviving candidate receives an additive score adjustment based on the user's recent `BehaviorSignal` rows (30-day window):

- **Positive signals** (`accepted_suggestion`, `repeated_meal_choice`, `repeated_activity`, positive value) boost candidates whose tokens overlap with the signal's `entity_name` by **+0.12 × overlap × signal_value**
- **Negative signals** (`rejected_suggestion`, `rejected_activity`, negative value) penalise overlapping candidates by **−0.15 × overlap × |signal_value|**
- **Diversity penalty** — candidates whose title closely matches a suggestion shown in the last 7 days receive a **−0.20** penalty (scaled by overlap for partial matches)

Final scores are clamped to `[0.0, 1.0]`.

### Stage 4 — Ranking and persistence

Candidates are sorted by score descending. The top-N are written as `Suggestion` rows with `status="pending"`. When the user responds (accepts/rejects), `SuggestionService.respond_to_suggestion()` updates the suggestion status and emits a new `BehaviorSignal` — closing the learning loop. It only accepts a suggestion that is still pending and that belongs to the person responding.

Household-scoped suggestions (pantry/shopping) skip user-level filtering and are stored with `scope_type="household"`.

---

## Shared / Household Data Design

The core tension in a two-person household app is: some data is naturally shared (we cooked the same dinner, we went to the gym together), but individual consumption, performance, and health metrics must stay separate.

GaiaPulse resolves this with a bridge-table pattern at two levels:

### Meal isolation

```
MealEvent (household-scoped)
  └── MealParticipant (per user)
        └── MealItemConsumed (per user, per food item)
```

A `MealEvent` records that a meal happened in the household at a specific time. Each user who participated gets a `MealParticipant` row, and each item they consumed gets a `MealItemConsumed` row linked to their `MealParticipant`. Nutritional queries, history, and recommendations always filter by `meal_participant.user_id` — never by `meal_event.household_id` alone.

### Workout isolation

```
WorkoutSession (household-scoped)
  └── WorkoutParticipant (per user)
        └── WorkoutExercise (per user, per exercise)
```

Identical pattern. A session records that a workout happened; each participant's exercises, loads, and effort scores are isolated to their `WorkoutExercise` rows.

### Pantry

Pantry stock and movements are household-scoped (both people share the same fridge). `PantryMovement` records which user performed the action via a nullable `user_id`, but the stock balance belongs to the household.

### User preferences and goals

All preference, dietary, and goal fields (`goals_json`, `dietary_preferences_json`, `dietary_restrictions_json`, `disliked_foods_json`, `preferred_activities_json`, etc.) live directly on the `User` model as JSONB columns. There is no shared preference model — each user's profile is entirely independent.

---

## Testing

```bash
pytest tests/
```

The test suite uses SQLite in-memory via a `conftest.py` fixture that overrides the database URL. No external services are required.

**48 tests** across 7 files:

| File | Coverage area |
|---|---|
| `test_nlp.py` | NLP rule parsing — meal, workout, metric, stock, preference intents |
| `test_pantry.py` | Pantry stock add/consume, low-stock detection |
| `test_meals.py` | Meal creation, per-user item isolation |
| `test_workouts.py` | Workout session and per-user exercise isolation |
| `test_body_metrics.py` | Body metric logging and retrieval |
| `test_recommendations.py` | Candidate scoring, hard constraint filtering, behavior signal learning |
| `test_notifications.py` | Notification creation, read/dismiss lifecycle |

Run with coverage:

```bash
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Known Tradeoffs & Roadmap

### Current tradeoffs

- **SQLite in tests, PostgreSQL in production** — JSONB columns use a shim in SQLite tests. A small number of JSONB-specific queries may behave differently. Column-level tests always pass but complex JSONB path queries are not integration-tested against SQLite.
- **Single migration** — The entire schema lives in `0001_initial_schema.py`. Future changes will require new Alembic revisions; the initial single-file approach made early iteration faster.
- **NLP Layer 2 requires OpenAI** — There is no local LLM fallback for Layer 2. If `OPENAI_API_KEY` is not set, ambiguous input falls back to the rule parser's best guess.
- **No real-time sync** — The shared household model assumes both users are hitting the same server. There is no WebSocket or SSE push; HTMX polling would need to be added for live shared updates.
- **Tailwind Play CDN** — Fast to iterate on but not suitable for a production bundle. A Tailwind CLI build step should replace the CDN link before deploying publicly.
- **Session tokens, not JWTs** — `itsdangerous` URLSafeTimedSerializer tokens are simpler than JWTs and avoid stateless token revocation complexity, but require the `APP_SECRET_KEY` to be stable and secret.
- **Two users only** — The data model supports arbitrary household members, but the NLP participant resolver only knows "diego" and "rocio" by name. Adding a third user would require updating the resolver patterns and seeding logic.

### Roadmap ideas

- Push notifications via Web Push API (the PWA manifest is already in place)
- Barcode scanning for food item lookup
- Import from Apple Health / Google Fit
- Weekly summary email digest
- Multi-household support (the schema already has `household_id` on all relevant tables)
- Replace Tailwind Play CDN with a proper build step
- Local LLM fallback (e.g. Ollama) for Layer 2 NLP when no OpenAI key is set
