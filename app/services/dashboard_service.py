from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import local_today, to_local
from app.repositories.body_metric_repo import BodyMetricRepository
from app.repositories.meal_repo import MealRepository
from app.repositories.pantry_repo import PantryStockRepository
from app.repositories.workout_repo import WorkoutRepository


class DashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.metric_repo = BodyMetricRepository(db)
        self.meal_repo = MealRepository(db)
        self.workout_repo = WorkoutRepository(db)
        self.pantry_repo = PantryStockRepository(db)

    def get_user_dashboard_data(self, user_id: int, household_id: int) -> dict[str, Any]:
        """Aggregate all dashboard data for a user."""
        weight_trend = self._weight_trend_data(user_id)
        workout_freq = self._workout_frequency_data(user_id, household_id)
        muscle_groups = self._muscle_group_data(user_id, household_id)
        meal_types = self._meal_types_data(user_id, household_id)
        active_days = self._active_days_data(user_id, household_id)
        pantry_summary = self._pantry_summary(household_id)

        return {
            "weight_trend": weight_trend,
            "workout_frequency": workout_freq,
            "muscle_groups": muscle_groups,
            "meal_types": meal_types,
            "active_days": active_days,
            "pantry_summary": pantry_summary,
        }

    def _weight_trend_data(self, user_id: int) -> dict[str, Any]:
        series = self.metric_repo.get_weight_series(user_id, days=30)
        return {
            #: La columna guarda UTC, así que un pesaje de las 22:00 de acá salía
            #: rotulado con la fecha de mañana — y el rótulo cambiaba según el motor,
            #: porque `strftime` sobre el valor crudo depende de cómo lo devolvió.
            "labels": [to_local(m.timestamp).strftime("%b %d") for m in series],
            "data": [float(m.weight_kg) if m.weight_kg else None for m in series],
        }

    def _workout_frequency_data(self, user_id: int, household_id: int) -> dict[str, Any]:
        """Workouts per week for last 4 weeks."""
        weeks = []
        counts = []
        today = local_today()
        for i in range(3, -1, -1):
            week_start = today - timedelta(days=today.weekday() + 7 * i)
            week_end = week_start + timedelta(days=6)
            # El corte de fin de semana va en el SQL desde que el repositorio acepta
            # `end_date`. Antes se pedían las 100 sesiones **más nuevas** desde el
            # inicio de la semana y se descartaban en Python las posteriores: con más
            # de 100 sesiones en cuatro semanas, el límite se agotaba con las recientes
            # y la barra de la semana más vieja daba 0.
            sessions = self.workout_repo.get_household_sessions(
                household_id,
                limit=100,
                user_id=user_id,
                start_date=week_start,
                end_date=week_end,
            )
            weeks.append(week_start.strftime("W%U"))
            counts.append(len(sessions))
        return {"labels": weeks, "data": counts}

    def _muscle_group_data(self, user_id: int, household_id: int) -> dict[str, Any]:
        groups = self.workout_repo.get_user_muscle_groups_trained(user_id, household_id, days=30)
        return {
            "labels": list(groups.keys()),
            "data": list(groups.values()),
        }

    def _meal_types_data(self, user_id: int, household_id: int) -> dict[str, Any]:
        """Count meals by type over last 7 days."""
        meals = self.meal_repo.get_household_meals(
            household_id,
            limit=200,
            user_id=user_id,
            start_date=local_today() - timedelta(days=6),
        )
        type_counts: dict[str, int] = {}
        for m in meals:
            type_counts[m.meal_type] = type_counts.get(m.meal_type, 0) + 1
        return {
            "labels": list(type_counts.keys()),
            "data": list(type_counts.values()),
        }

    def _active_days_data(self, user_id: int, household_id: int) -> dict[str, Any]:
        """Last 30 days: active=had workout, inactive=no workout."""
        sessions = self.workout_repo.get_user_recent_sessions(user_id, household_id, days=30)
        #: El día **local** de cada sesión. `timestamp_start.date()` sobre una
        #: columna aware da el día UTC, así que un entrenamiento de las 21:00 de
        #: acá pintaba el cuadradito del día siguiente en el calendario.
        active_dates = {to_local(s.timestamp_start).date() for s in sessions}
        today = local_today()
        days_data = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            days_data.append(
                {
                    "date": d.isoformat(),
                    "active": d in active_dates,
                }
            )
        return {"days": days_data}

    def _pantry_summary(self, household_id: int) -> dict[str, Any]:
        all_stock = self.pantry_repo.get_household_stock(household_id)
        low_stock = [s for s in all_stock if s.is_low]
        return {
            "total_items": len(all_stock),
            "low_stock_count": len(low_stock),
            "low_stock_items": [s.food_item.canonical_name for s in low_stock[:5]],
        }
