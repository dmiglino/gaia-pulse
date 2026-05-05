"""Tests for workout logging."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.household import Household
from app.models.user import User
from app.schemas.workout import WorkoutExerciseCreate, WorkoutParticipantCreate, WorkoutSessionCreate
from app.services.workout_service import WorkoutService


def now() -> datetime:
    return datetime.now(timezone.utc)


class TestWorkoutLogging:
    def test_log_workout_single_user(self, db: Session, household: Household, diego: User) -> None:
        svc = WorkoutService(db)
        data = WorkoutSessionCreate(
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
                            distance_km=12.5,
                        )
                    ]
                )
            ]
        )
        session = svc.log_workout(household.id, data)
        assert session.id is not None
        assert session.duration_minutes == 40

        loaded = svc.get_session(session.id)
        assert len(loaded.participants) == 1
        assert loaded.participants[0].user_id == diego.id
        assert len(loaded.participants[0].exercises) == 1

    def test_log_shared_workout(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        svc = WorkoutService(db)
        data = WorkoutSessionCreate(
            timestamp_start=now(),
            duration_minutes=60,
            workout_type="gym",
            participants=[
                WorkoutParticipantCreate(
                    user_id=diego.id,
                    exercises=[
                        WorkoutExerciseCreate(exercise_name="Bench Press", muscle_group="chest", sets=3, reps=10),
                        WorkoutExerciseCreate(exercise_name="Tricep Pushdown", muscle_group="arms", sets=3, reps=12),
                    ]
                ),
                WorkoutParticipantCreate(
                    user_id=rocio.id,
                    exercises=[
                        WorkoutExerciseCreate(exercise_name="Yoga", muscle_group="full_body"),
                    ]
                ),
            ]
        )
        session = svc.log_workout(household.id, data)
        loaded = svc.get_session(session.id)
        assert len(loaded.participants) == 2

        diego_part = next(p for p in loaded.participants if p.user_id == diego.id)
        rocio_part = next(p for p in loaded.participants if p.user_id == rocio.id)

        assert len(diego_part.exercises) == 2
        assert len(rocio_part.exercises) == 1
        assert rocio_part.exercises[0].exercise_name == "Yoga"
        # Diego should NOT have Yoga
        assert not any(e.exercise_name == "Yoga" for e in diego_part.exercises)

    def test_workout_only_one_user(
        self, db: Session, household: Household, diego: User, rocio: User
    ) -> None:
        """Critical: Diego biking alone should NOT include Rocío."""
        svc = WorkoutService(db)
        data = WorkoutSessionCreate(
            timestamp_start=now(),
            duration_minutes=40,
            workout_type="cycling",
            participants=[
                WorkoutParticipantCreate(user_id=diego.id, exercises=[])
            ]
        )
        session = svc.log_workout(household.id, data)
        loaded = svc.get_session(session.id)
        assert len(loaded.participants) == 1
        assert loaded.participants[0].user_id == diego.id
        assert not any(p.user_id == rocio.id for p in loaded.participants)
