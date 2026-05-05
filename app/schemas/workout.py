from datetime import datetime

from pydantic import BaseModel


class WorkoutExerciseCreate(BaseModel):
    exercise_name: str
    muscle_group: str | None = None
    sets: int | None = None
    reps: int | None = None
    load_kg: float | None = None
    duration_minutes: int | None = None
    distance_km: float | None = None
    perceived_effort: int | None = None
    notes: str | None = None


class WorkoutParticipantCreate(BaseModel):
    user_id: int
    exercises: list[WorkoutExerciseCreate] = []
    notes: str | None = None


class WorkoutSessionCreate(BaseModel):
    timestamp_start: datetime
    duration_minutes: int | None = None
    workout_type: str | None = None
    location: str | None = None
    calories_estimated: int | None = None
    participants: list[WorkoutParticipantCreate]
    notes: str | None = None
    source: str = "manual"


class WorkoutExerciseRead(BaseModel):
    id: int
    exercise_name: str
    muscle_group: str | None
    sets: int | None
    reps: int | None
    load_kg: float | None
    duration_minutes: int | None
    distance_km: float | None
    perceived_effort: int | None
    notes: str | None

    model_config = {"from_attributes": True}


class WorkoutParticipantRead(BaseModel):
    id: int
    user_id: int
    notes: str | None
    exercises: list[WorkoutExerciseRead]

    model_config = {"from_attributes": True}


class WorkoutSessionRead(BaseModel):
    id: int
    household_id: int
    timestamp_start: datetime
    timestamp_end: datetime | None
    duration_minutes: int | None
    workout_type: str | None
    location: str | None
    calories_estimated: int | None
    source: str
    notes: str | None
    participants: list[WorkoutParticipantRead]
    created_at: datetime

    model_config = {"from_attributes": True}
