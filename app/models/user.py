from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.blood_analysis import BloodAnalysis
    from app.models.body_metric import BodyMetricLog
    from app.models.household import Household
    from app.models.meal import MealParticipant
    from app.models.nlp import NLPIngestionEvent
    from app.models.notification import Notification
    from app.models.pantry import PantryMovement
    from app.models.signal import BehaviorSignal
    from app.models.suggestion import RecommendationPreference, Suggestion
    from app.models.workout import WorkoutParticipant


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Physical profile
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )  # male/female/other/prefer_not_to_say
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    target_weight_kg: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Activity / wellness profile
    baseline_activity_level: Mapped[str] = mapped_column(
        String(30), default="moderate", nullable=False
    )  # sedentary/light/moderate/active/very_active
    goals_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Objetivo nutricional declarado (fase 7.6). No es prescripción clínica — ver el
    # disclaimer en `profile/index.html` — y ninguno es obligatorio: sin ellos, la
    # tarjeta de macros sigue comparando contra el propio promedio.
    goal_protein_g: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    goal_fiber_g: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    goal_calories_kcal: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Food preferences
    dietary_preferences_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    dietary_restrictions_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    disliked_foods_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    preferred_cuisines_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Activity preferences
    preferred_activities_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    impossible_activities_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    disliked_activities_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Recommendation context
    recommendation_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Onboarding
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )

    # Display preferences
    avatar_color: Mapped[str] = mapped_column(String(20), default="#6366f1", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # relationships
    household: Mapped["Household"] = relationship("Household", back_populates="users")
    body_metrics: Mapped[list["BodyMetricLog"]] = relationship(
        "BodyMetricLog", back_populates="user", order_by="BodyMetricLog.timestamp.desc()"
    )
    meal_participations: Mapped[list["MealParticipant"]] = relationship(
        "MealParticipant", back_populates="user"
    )
    workout_participations: Mapped[list["WorkoutParticipant"]] = relationship(
        "WorkoutParticipant", back_populates="user"
    )
    pantry_movements: Mapped[list["PantryMovement"]] = relationship(
        "PantryMovement", back_populates="user"
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        "Suggestion",
        back_populates="user",
        foreign_keys="Suggestion.scope_user_id",
    )
    recommendation_preferences: Mapped[list["RecommendationPreference"]] = relationship(
        "RecommendationPreference", back_populates="user"
    )
    behavior_signals: Mapped[list["BehaviorSignal"]] = relationship(
        "BehaviorSignal", back_populates="user"
    )
    nlp_events: Mapped[list["NLPIngestionEvent"]] = relationship(
        "NLPIngestionEvent", back_populates="user"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        foreign_keys="Notification.user_id",
    )
    blood_analyses: Mapped[list["BloodAnalysis"]] = relationship(
        "BloodAnalysis",
        back_populates="user",
        order_by="BloodAnalysis.analysis_date.desc()",
    )

    @property
    def display_name(self) -> str:
        return self.name.split()[0] if self.name else "User"

    def __repr__(self) -> str:
        return f"<User id={self.id} name={self.name!r}>"
