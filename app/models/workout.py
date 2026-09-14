from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.household import Household
    from app.models.user import User


class ExerciseType(Base):
    """Catalog of exercise types."""

    __tablename__ = "exercise_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # strength/cardio/flexibility/sports/yoga/other
    muscle_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    indoor_outdoor: Mapped[str] = mapped_column(
        String(20), default="both", nullable=False
    )  # indoor/outdoor/both
    equipment_required: Mapped[str | None] = mapped_column(String(200), nullable=True)
    intensity: Mapped[str] = mapped_column(
        String(20), default="moderate", nullable=False
    )  # low/moderate/high/variable
    tags_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases_json: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )  # ["press de banca"] — mismo shape que FoodItem.aliases_json (0004)

    def __repr__(self) -> str:
        return f"<ExerciseType id={self.id} name={self.name!r}>"


class WorkoutSession(Base):
    """A workout session — potentially shared by multiple participants."""

    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    timestamp_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workout_type: Mapped[str | None] = mapped_column(
        String(60), nullable=True
    )  # gym/home/outdoor/sports/yoga/etc.
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calories_estimated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(
        String(30), default="manual", nullable=False
    )  # manual/text/voice/device_import
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # relationships
    household: Mapped["Household"] = relationship("Household")
    participants: Mapped[list["WorkoutParticipant"]] = relationship(
        "WorkoutParticipant", back_populates="workout_session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<WorkoutSession id={self.id} type={self.workout_type} ts={self.timestamp_start}>"


class WorkoutParticipant(Base):
    """A specific user's participation in a workout session."""

    __tablename__ = "workout_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    workout_session: Mapped["WorkoutSession"] = relationship(
        "WorkoutSession", back_populates="participants"
    )
    user: Mapped["User"] = relationship("User", back_populates="workout_participations")
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        "WorkoutExercise", back_populates="workout_participant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<WorkoutParticipant session={self.workout_session_id} user={self.user_id}>"


class WorkoutExercise(Base):
    """An individual exercise performed by a participant within a session."""

    __tablename__ = "workout_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workout_participant_id: Mapped[int] = mapped_column(
        ForeignKey("workout_participants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exercise_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("exercise_types.id", ondelete="SET NULL"), nullable=True
    )
    exercise_name: Mapped[str] = mapped_column(String(120), nullable=False)  # denormalized for display
    sets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_kg: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    perceived_effort: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-10
    muscle_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    workout_participant: Mapped["WorkoutParticipant"] = relationship(
        "WorkoutParticipant", back_populates="exercises"
    )
    exercise_type: Mapped["ExerciseType | None"] = relationship("ExerciseType")

    def __repr__(self) -> str:
        return f"<WorkoutExercise name={self.exercise_name!r} sets={self.sets} reps={self.reps}>"
