"""Web-layer tests for the meal / workout / body-metric delete actions.

The JSON API already had `DELETE /api/{meals,workouts,body-metrics}/{id}` — this
covers the web routes added to reach the same service methods from a plain
`<form method="post">`, mirroring `POST /health/{analysis_id}/delete`.
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.i18n import _
from app.models.household import Household
from app.models.user import User
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.meal_repo import MealRepository
from app.repositories.workout_repo import WorkoutRepository
from app.schemas.body_metric import BodyMetricCreate
from app.schemas.meal import MealEventCreate, MealItemCreate, MealParticipantCreate
from app.schemas.workout import (
    WorkoutExerciseCreate,
    WorkoutParticipantCreate,
    WorkoutSessionCreate,
)
from app.services.body_metric_service import BodyMetricService
from app.services.meal_service import MealService
from app.services.workout_service import WorkoutService
from app.web.flash import FLASH_COOKIE_NAME


def now() -> datetime:
    return datetime.now(UTC)


def test_meal_delete_removes_it_and_redirects_with_flash(
    authenticated_client: TestClient, db: Session, household: Household, diego: User
) -> None:
    event = MealService(db).log_meal(
        household.id,
        MealEventCreate(
            timestamp=now(),
            meal_type="dinner",
            context="home",
            participants=[
                MealParticipantCreate(
                    user_id=diego.id,
                    items=[MealItemCreate(food_name="milanesa", quantity=1, unit="serving")],
                )
            ],
        ),
    )

    r = authenticated_client.post(f"/meals/{event.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/meals"
    assert FLASH_COOKIE_NAME in r.cookies

    r = authenticated_client.get("/meals/")
    assert _("Meal deleted.") in r.text
    assert MealRepository(db).get(event.id) is None


def test_meal_delete_cannot_reach_another_households_meal(
    authenticated_client: TestClient, db: Session
) -> None:
    other_home = Household(name="Someone else", timezone="UTC")
    db.add(other_home)
    db.flush()
    other_user = User(
        household_id=other_home.id,
        name="Otra persona",
        email="otra@test.com",
        password_hash="x",
        onboarding_completed=True,
    )
    db.add(other_user)
    db.flush()

    event = MealService(db).log_meal(
        other_home.id,
        MealEventCreate(
            timestamp=now(),
            meal_type="lunch",
            context="home",
            participants=[
                MealParticipantCreate(
                    user_id=other_user.id,
                    items=[MealItemCreate(food_name="ensalada", quantity=1, unit="serving")],
                )
            ],
        ),
    )

    r = authenticated_client.post(f"/meals/{event.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/meals"

    r = authenticated_client.get("/meals/")
    assert _("That meal is not available.") in r.text
    assert MealRepository(db).get(event.id) is not None


def test_workout_delete_removes_it_and_redirects_with_flash(
    authenticated_client: TestClient, db: Session, household: Household, diego: User
) -> None:
    session = WorkoutService(db).log_workout(
        household.id,
        WorkoutSessionCreate(
            timestamp_start=now(),
            duration_minutes=40,
            workout_type="cycling",
            participants=[
                WorkoutParticipantCreate(
                    user_id=diego.id,
                    exercises=[
                        WorkoutExerciseCreate(
                            exercise_name="Cycling",
                            muscle_group="legs",
                            duration_minutes=40,
                        )
                    ],
                )
            ],
        ),
    )

    r = authenticated_client.post(f"/workouts/{session.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/workouts"
    assert FLASH_COOKIE_NAME in r.cookies

    r = authenticated_client.get("/workouts/")
    assert _("Workout deleted.") in r.text
    assert WorkoutRepository(db).get(session.id) is None


def test_workout_delete_cannot_reach_another_households_workout(
    authenticated_client: TestClient, db: Session
) -> None:
    other_home = Household(name="Someone else", timezone="UTC")
    db.add(other_home)
    db.flush()
    other_user = User(
        household_id=other_home.id,
        name="Otra persona",
        email="otra2@test.com",
        password_hash="x",
        onboarding_completed=True,
    )
    db.add(other_user)
    db.flush()

    session = WorkoutService(db).log_workout(
        other_home.id,
        WorkoutSessionCreate(
            timestamp_start=now(),
            duration_minutes=20,
            workout_type="running",
            participants=[
                WorkoutParticipantCreate(
                    user_id=other_user.id,
                    exercises=[WorkoutExerciseCreate(exercise_name="Running", muscle_group="legs")],
                )
            ],
        ),
    )

    r = authenticated_client.post(f"/workouts/{session.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/workouts"

    r = authenticated_client.get("/workouts/")
    assert _("That workout is not available.") in r.text
    assert WorkoutRepository(db).get(session.id) is not None


def test_body_metric_delete_removes_it_and_redirects_with_flash(
    authenticated_client: TestClient, db: Session, diego: User
) -> None:
    metric = BodyMetricService(db).log_metric(
        diego.id, BodyMetricCreate(timestamp=now(), weight_kg=82.5)
    )

    r = authenticated_client.post(f"/body-metrics/{metric.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/body-metrics"
    assert FLASH_COOKIE_NAME in r.cookies

    r = authenticated_client.get("/body-metrics/")
    assert _("Log entry deleted.") in r.text
    assert BodyMetricRepository(db).get(metric.id) is None


def test_body_metric_delete_cannot_reach_a_household_partners_metric(
    authenticated_client: TestClient, db: Session, rocio: User
) -> None:
    """Body metrics are personal, unlike meals and workouts: sharing a household
    does not grant Diego the ability to delete Rocío's weigh-in, even though the
    household tabs let him *look* at it."""
    metric = BodyMetricService(db).log_metric(
        rocio.id, BodyMetricCreate(timestamp=now(), weight_kg=60.0)
    )

    r = authenticated_client.post(f"/body-metrics/{metric.id}/delete", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/body-metrics"

    r = authenticated_client.get("/body-metrics/")
    assert _("That log entry is not available.") in r.text
    assert BodyMetricRepository(db).get(metric.id) is not None
