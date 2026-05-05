from datetime import datetime

from pydantic import BaseModel


class BodyMetricCreate(BaseModel):
    timestamp: datetime
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    muscle_mass_kg: float | None = None
    waist_cm: float | None = None
    sleep_hours: float | None = None
    notes: str | None = None


class BodyMetricRead(BaseModel):
    id: int
    user_id: int
    timestamp: datetime
    weight_kg: float | None
    body_fat_pct: float | None
    muscle_mass_kg: float | None
    waist_cm: float | None
    sleep_hours: float | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BodyMetricTrend(BaseModel):
    user_id: int
    dates: list[str]
    weights: list[float | None]
    latest_weight: float | None
    weight_change_30d: float | None  # positive = gain, negative = loss
    latest_body_fat: float | None
    latest_waist: float | None
