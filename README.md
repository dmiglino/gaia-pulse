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

Candidates are sorted by score descending, and subjects that are still suppressed are dropped before any row is written (`RecommendationEngine._still_suppressed`): a subject whose previous suggestion is still `pending`, or whose `snoozed_until` has not yet passed. This is a filter, not a score adjustment — a suppressed subject does not appear at all, at any score. The top-N of what survives are written as `Suggestion` rows with `status="pending"`.

`SuggestionService.respond_to_suggestion()` records the answer. It only accepts a suggestion that is still pending and that belongs to the person responding.

- **`accepted`**, **`rejected`**, and **`dismissed`** each emit one `BehaviorSignal` for the suggestion's subject — `accepted_suggestion` (**+1.0**), `rejected_suggestion` (**−1.0**), and `ignored_suggestion` (**−0.3**) respectively — closing the learning loop
- **`snoozed`** emits none: "later" is a statement about the moment, not about the subject
- **`snoozed`** and **`dismissed`** also set `snoozed_until` **3 days** ahead (`SuggestionService._SNOOZE_DAYS`), which is what the suppression filter above reads on the next run. Nothing has to un-snooze the row — the condition is evaluated against the clock on every run

A response may also carry a free-text reason (`feedback_notes`, written in the collapsed form on the suggestion card and capped at **500 characters** in both the web route and the schema). The reason is matched against the closed vocabularies of known food names and known activity names, and every subject it names — other than the card's own subject, already recorded above — emits one `explicit_preference` signal with the same value as the response itself, so "we do not like broccoli" teaches about broccoli rather than about the wording of the card. A food is matched by any of its names and recorded under its canonical one, which matters because the catalogue convention is an English canonical with the Spanish as an alias (`["tomato", "tomate"]`) while candidates declare `canonical_name` as their subject — "no nos gusta la palta" has to reach a candidate named `avocado`. Mined subjects only move the score: they never filter a candidate, since `learning.rejected_subjects` reads `rejected_suggestion` only. `snoozed` carries no value and therefore mines nothing.

At most **5** subjects are mined per reason (`SuggestionService._MAX_MINED_SUBJECTS`) — 500 characters are enough to name dozens of catalogue foods, and `behavior_signals` has no pruning job. The reason text itself is stored once, in `suggestions.feedback_notes`; no signal copies it — a mined one records `{"mined_from": "feedback_notes"}`, the card's own one records `{"reason_written": true}` — and both point back at the row through `source_entity_id`, so deleting the reason deletes it. The sign is one per sentence, so "we do not like broccoli, we prefer chicken" records **−1.0** for both; that is tolerable precisely because mined subjects order rather than filter.

Household-scoped suggestions (pantry/shopping) skip user-level filtering and are stored with `scope_type="household"`.

### Stage 5 — Seeing it and taking it back

Everything above happens in a background job, which means the person it is about never sees it. `/profile/` renders what the engine actually reads, from `LearningService.learned_profile()`: every subject with a signal inside the horizon, grouped by subject type, with the direction, the confidence label, how many records back it, how many of those were **words** and not behaviour, and how long ago the last one was. Below the subjects sit the category-level conclusions (Stage 3's attribute generalization), which is where "we rejected broccoli, cauliflower and kale" becomes an opinion about vegetables.

Four things the panel is precise about, because each one was a way of showing something false:

- **Only the five types in `learning.SUBJECT_TYPES`** (`food`, `exercise`, `muscle_group`, `biomarker`, `habit`) — the ones the scorer knows how to read. A signal of any other `entity_type` is inert, and listing it in a table ordered by "this is what is moving your suggestions most" would say otherwise. A test (`test_every_subject_type_the_engine_learns_has_a_place_in_the_panel`) ties `SUBJECT_TYPES` to the panel's group order, and another ties each type to its own label, icon and tone, so adding a sixth type cannot quietly render an English `| title` heading in a Spanish app.
- **The word count is `signal_type="explicit_preference"`, not `source_type="explicit"`.** The latter is also how `respond_to_suggestion` marks a **tap** on a card, so counting it printed "1 from what you said" next to a food nobody had written a word about — which is precisely the distinction the line exists to draw.
- **The cutoffs live in Python, not in the template.** `SubjectAffinity.direction_band` (`toward` / `away` / `mixed`, edge at ±0.2) and `.confidence_band` (`plenty` / `some` / `new`, at 3× and 1× `half_saturation`) return words; the macros in `components/domain.html` only map word → label, and an unknown band renders **nothing** on purpose. A `{% else %}` that labelled a new band with the nearest existing label would be wrong and silent; a gap is visible.
- **A declared preference is shown but has no Forget button.** A row is `declared` when its `explicit_preference` signal has no source suggestion, which is only `save_preference` — the one path that writes the `recommendation_preferences` row in the same transaction. Forgetting deletes signals, so the preference would survive (still *filtering*, not reordering, if it is a dislike), and no route deletes it. The row points at `#what-you-told-us` instead, which is where it can actually be changed.

`POST /profile/learned/forget` deletes a subject's signals for the acting user and returns the recalculated panel. There is no "forgotten" column and none is needed: what has been learned *is* the set of signals, so removing them is what forgetting means. Two consequences the screen states rather than hides:

- **Forgetting is not a ban.** The next matching meal, purchase or workout teaches the same thing again. A permanent exclusion is a dietary restriction or an impossible activity — those filter candidates instead of reordering them.
- **A category has no button**, because it has no rows of its own: `ATTRIBUTE_SUBJECT_TYPES` sits outside `SUBJECT_TYPES`, so `record_signal` rejects it and the category is derived from the food catalogue on every read. It goes away when the items behind it do.

The subject ids to delete are resolved in Python, not in SQL: `BehaviorSignalRepository.record` stores `entity_name.lower()` with its accents (`brócoli`) while the form submits the comparable form from `learning.normalize_subject` (`brocoli`). A `WHERE entity_name = ?` fed by the form would delete nothing and report success. Zero deletions is reported as zero deletions.

So the row shows one name and submits another: the visible text is `LearnedSubject.display_name` — the accented spelling from the most recent signal, because `brocoli` printed in a Spanish app reads as a bug in the app — and the hidden field carries the normalized key, which is what matches across both spellings. For the same reason the confirmation echoes the name **as it was stored** (`Forgotten.subject_name`) and not what arrived in the form: a hand-written `PÓLLO!!!` deletes the rows of `pollo`, and answering "Forgotten: PÓLLO!!!" would hand back text the person never saved, about an action that cannot be undone.

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

**509 tests** across 24 files:

| File | Coverage area |
|---|---|
| `test_nlp.py` | NLP rule parsing — meal, workout, metric, stock, preference intents |
| `test_nlp_service.py` | The two-layer pipeline, the confirmation gate, the replay guard |
| `test_nlp_openai_adapter.py` | Layer 2 — function schema, deserialization, failure fallback |
| `test_pantry.py` | Pantry stock add/consume, low-stock detection |
| `test_meals.py` | Meal creation, per-user item isolation |
| `test_workouts.py` | Workout session and per-user exercise isolation |
| `test_body_metrics.py` | Body metric logging and retrieval |
| `test_recommendations.py` | Candidate scoring, hard constraint filtering, subject suppression |
| `test_learning_signals.py` | The learning axes — affinity, decay, attribute level, slot, satiety, reason mining |
| `test_notifications.py` | Notification creation, read/dismiss lifecycle |
| `test_notification_jobs.py` | The scheduled jobs — absences detected, subject dedup, escalation, retirement |
| `test_clock.py` | Local time, quiet hours, day bounds |
| `test_actions.py` | Every notification and suggestion resolving to one primary action |
| `test_dashboard.py` | Dashboard aggregation and chart series |
| `test_components.py` | The Jinja macro library — `ui`, `icons`, `domain` |
| `test_web_auth.py` | Login, logout, session cookie, the redirect a page route owes |
| `test_web_pages.py` | Smoke of every page route against 200 |
| `test_web_pages_populated.py` | The same pages with data in them |
| `test_web_capture.py` | The capture flow end to end, including the confirmation screen |
| `test_web_fragments.py` | The HTMX fragments and the routes their `hx-*` attributes point at |
| `test_web_history.py` | History tabs and their cards |
| `test_web_onboarding.py` | The four-step wizard and the gate that forces it |
| `test_web_profile.py` | Profile reads and writes, declared preferences |
| `test_web_learned_panel.py` | The learned panel and forgetting a subject |

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
