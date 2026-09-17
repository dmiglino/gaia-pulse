"""Tests for DashboardService aggregation methods."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import local_day_bounds, local_today
from app.models.body_metric import BodyMetricLog
from app.models.food import FoodItem
from app.models.household import Household
from app.models.meal import MealEvent, MealParticipant
from app.models.pantry import PantryStock
from app.models.user import User
from app.models.workout import WorkoutExercise, WorkoutParticipant, WorkoutSession
from app.services.dashboard_service import DashboardService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_weight(db: Session, user: User, weight_kg: float, days_ago: int = 0) -> BodyMetricLog:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    m = BodyMetricLog(user_id=user.id, timestamp=ts, weight_kg=weight_kg)
    db.add(m)
    db.flush()
    return m


def _add_workout(
    db: Session,
    household: Household,
    user: User,
    days_ago: int = 0,
    duration: int = 45,
    muscle_group: str | None = None,
    ts: datetime | None = None,
) -> WorkoutSession:
    # `ts` explícito para los tests que necesitan caer en un día local concreto y no
    # a "hace N días": los cortes del dashboard son días locales, así que restar días
    # de `now()` deja la sesión del lado que le toque según la hora en que se corra.
    if ts is None:
        ts = datetime.now(UTC) - timedelta(days=days_ago)
    session = WorkoutSession(
        household_id=household.id,
        timestamp_start=ts,
        duration_minutes=duration,
        workout_type="gym",
    )
    db.add(session)
    db.flush()
    participant = WorkoutParticipant(workout_session_id=session.id, user_id=user.id)
    db.add(participant)
    db.flush()
    if muscle_group:
        ex = WorkoutExercise(
            workout_session_id=session.id,
            workout_participant_id=participant.id,
            exercise_name="test exercise",
            muscle_group=muscle_group,
        )
        db.add(ex)
        db.flush()
    return session


def _add_meal(
    db: Session,
    household: Household,
    user: User,
    meal_type: str = "dinner",
    days_ago: int = 0,
) -> MealEvent:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    event = MealEvent(household_id=household.id, timestamp=ts, meal_type=meal_type)
    db.add(event)
    db.flush()
    participant = MealParticipant(meal_event_id=event.id, user_id=user.id)
    db.add(participant)
    db.flush()
    return event


# ---------------------------------------------------------------------------
# Weight trend
# ---------------------------------------------------------------------------


class TestWeightTrend:
    def test_empty_returns_empty_series(
        self, db: Session, diego: User, household: Household
    ) -> None:
        svc = DashboardService(db)
        data = svc._weight_trend_data(diego.id)
        assert data["labels"] == []
        assert data["data"] == []

    def test_single_measurement(self, db: Session, diego: User, household: Household) -> None:
        _add_weight(db, diego, 82.5, days_ago=0)
        svc = DashboardService(db)
        data = svc._weight_trend_data(diego.id)
        assert len(data["labels"]) == 1
        assert abs(data["data"][0] - 82.5) < 0.01

    def test_multiple_measurements_sorted_asc(
        self, db: Session, diego: User, household: Household
    ) -> None:
        _add_weight(db, diego, 82.0, days_ago=10)
        _add_weight(db, diego, 81.5, days_ago=5)
        _add_weight(db, diego, 81.0, days_ago=1)
        svc = DashboardService(db)
        data = svc._weight_trend_data(diego.id)
        assert len(data["data"]) == 3
        # Should be in chronological order (oldest first)
        assert data["data"][0] >= data["data"][-1]  # weight decreasing over time

    def test_only_current_users_data(
        self, db: Session, diego: User, rocio: User, household: Household
    ) -> None:
        _add_weight(db, diego, 82.0, days_ago=1)
        _add_weight(db, rocio, 60.0, days_ago=1)
        svc = DashboardService(db)
        data = svc._weight_trend_data(diego.id)
        assert all(v is None or abs(v - 82.0) < 1.0 for v in data["data"] if v is not None)


# ---------------------------------------------------------------------------
# Workout frequency
# ---------------------------------------------------------------------------


class TestWorkoutFrequency:
    def test_no_workouts_returns_zeros(
        self, db: Session, diego: User, household: Household
    ) -> None:
        svc = DashboardService(db)
        data = svc._workout_frequency_data(diego.id, household.id)
        assert data["labels"] != []
        assert all(c == 0 for c in data["data"])

    def test_workout_counted_in_correct_week(
        self, db: Session, diego: User, household: Household
    ) -> None:
        """Una sesión de esta semana local tiene que caer en la última barra.

        Antes la sesión se plantaba con `days_ago=1`, que los lunes cae en la semana
        anterior: el test fallaba un día de cada siete. Se ancla al primer instante de
        la semana local en curso, que además es el borde donde un off-by-one se vería.
        """
        today = local_today()
        week_start = today - timedelta(days=today.weekday())
        _add_workout(db, household, diego, ts=local_day_bounds(week_start)[0])
        svc = DashboardService(db)
        data = svc._workout_frequency_data(diego.id, household.id)
        # Current week (last element) should have at least 1 workout
        assert data["data"][-1] >= 1
        assert len(data["labels"]) == 4


# ---------------------------------------------------------------------------
# Muscle groups
# ---------------------------------------------------------------------------


class TestMuscleGroups:
    def test_empty_returns_empty(self, db: Session, diego: User, household: Household) -> None:
        svc = DashboardService(db)
        data = svc._muscle_group_data(diego.id, household.id)
        assert data["labels"] == []
        assert data["data"] == []

    def test_muscle_groups_aggregated(self, db: Session, diego: User, household: Household) -> None:
        _add_workout(db, household, diego, days_ago=1, muscle_group="chest")
        _add_workout(db, household, diego, days_ago=2, muscle_group="chest")
        _add_workout(db, household, diego, days_ago=3, muscle_group="back")
        svc = DashboardService(db)
        data = svc._muscle_group_data(diego.id, household.id)
        assert "chest" in data["labels"]
        chest_idx = data["labels"].index("chest")
        assert data["data"][chest_idx] == 2


# ---------------------------------------------------------------------------
# Meal types
# ---------------------------------------------------------------------------


class TestMealTypes:
    def test_empty_returns_empty(self, db: Session, diego: User, household: Household) -> None:
        svc = DashboardService(db)
        data = svc._meal_types_data(diego.id, household.id)
        assert data["labels"] == []
        assert data["data"] == []

    def test_meal_types_counted(self, db: Session, diego: User, household: Household) -> None:
        _add_meal(db, household, diego, meal_type="dinner", days_ago=0)
        _add_meal(db, household, diego, meal_type="dinner", days_ago=1)
        _add_meal(db, household, diego, meal_type="breakfast", days_ago=2)
        svc = DashboardService(db)
        data = svc._meal_types_data(diego.id, household.id)
        assert "dinner" in data["labels"]
        dinner_idx = data["labels"].index("dinner")
        assert data["data"][dinner_idx] == 2


# ---------------------------------------------------------------------------
# Active days
# ---------------------------------------------------------------------------


class TestActiveDays:
    def test_returns_30_days(self, db: Session, diego: User, household: Household) -> None:
        svc = DashboardService(db)
        data = svc._active_days_data(diego.id, household.id)
        assert len(data["days"]) == 30

    def test_active_day_marked_correctly(
        self, db: Session, diego: User, household: Household
    ) -> None:
        _add_workout(db, household, diego, days_ago=0)
        svc = DashboardService(db)
        data = svc._active_days_data(diego.id, household.id)
        today_entry = data["days"][-1]  # last entry is today
        assert today_entry["active"] is True

    def test_inactive_days_not_active(self, db: Session, diego: User, household: Household) -> None:
        svc = DashboardService(db)
        data = svc._active_days_data(diego.id, household.id)
        assert all(not d["active"] for d in data["days"])


# ---------------------------------------------------------------------------
# Pantry summary
# ---------------------------------------------------------------------------


class TestPantrySummary:
    def test_empty_pantry(self, db: Session, household: Household) -> None:
        svc = DashboardService(db)
        data = svc._pantry_summary(household.id)
        assert data["total_items"] == 0
        assert data["low_stock_count"] == 0
        assert data["low_stock_items"] == []

    def test_pantry_with_items(self, db: Session, banana: FoodItem, household: Household) -> None:
        stock = PantryStock(
            household_id=household.id,
            food_item_id=banana.id,
            current_quantity=10,
            unit="unit",
            low_stock_threshold=3,
        )
        db.add(stock)
        db.flush()
        svc = DashboardService(db)
        data = svc._pantry_summary(household.id)
        assert data["total_items"] == 1
        assert data["low_stock_count"] == 0

    def test_low_stock_detected(self, db: Session, banana: FoodItem, household: Household) -> None:
        stock = PantryStock(
            household_id=household.id,
            food_item_id=banana.id,
            current_quantity=1,
            unit="unit",
            low_stock_threshold=3,
        )
        db.add(stock)
        db.flush()
        svc = DashboardService(db)
        data = svc._pantry_summary(household.id)
        assert data["low_stock_count"] == 1
        assert "banana" in data["low_stock_items"]


# ---------------------------------------------------------------------------
# Full aggregation
# ---------------------------------------------------------------------------


class TestGetUserDashboardData:
    def test_returns_all_keys(self, db: Session, diego: User, household: Household) -> None:
        svc = DashboardService(db)
        data = svc.get_user_dashboard_data(diego.id, household.id)
        expected_keys = {
            "weight_trend",
            "workout_frequency",
            "muscle_groups",
            "meal_types",
            "active_days",
            "pantry_summary",
        }
        assert set(data.keys()) == expected_keys

    def test_each_section_has_expected_shape(
        self, db: Session, diego: User, household: Household
    ) -> None:
        svc = DashboardService(db)
        data = svc.get_user_dashboard_data(diego.id, household.id)
        assert "labels" in data["weight_trend"] and "data" in data["weight_trend"]
        assert "labels" in data["workout_frequency"] and "data" in data["workout_frequency"]
        assert "labels" in data["muscle_groups"] and "data" in data["muscle_groups"]
        assert "labels" in data["meal_types"] and "data" in data["meal_types"]
        assert "days" in data["active_days"]
        assert "total_items" in data["pantry_summary"]
