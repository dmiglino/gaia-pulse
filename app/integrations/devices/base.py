"""Device integration adapter base.

Future implementations:
- Garmin Connect adapter
- Apple Health (via export file)
- Google Fit adapter
- Fitbit adapter

Each adapter produces standardized DeviceActivity / DeviceBodyMetric objects
that are then processed through the same service layer.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DeviceActivity:
    timestamp: datetime
    activity_type: str
    duration_minutes: int | None
    calories: int | None
    distance_km: float | None
    heart_rate_avg: int | None
    source_device: str


@dataclass
class DeviceBodyMetric:
    timestamp: datetime
    weight_kg: float | None
    body_fat_pct: float | None
    source_device: str


class DeviceAdapter(ABC):
    """Abstract base for wearable / health device integrations."""

    @abstractmethod
    async def get_recent_activities(self, user_id: int, days: int = 7) -> list[DeviceActivity]: ...

    @abstractmethod
    async def get_recent_body_metrics(
        self, user_id: int, days: int = 7
    ) -> list[DeviceBodyMetric]: ...
