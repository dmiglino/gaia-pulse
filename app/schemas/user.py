from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, field_validator


class UserBase(BaseModel):
    name: str
    email: EmailStr
    height_cm: float | None = None
    target_weight_kg: float | None = None
    baseline_activity_level: str = "moderate"
    birth_date: date | None = None
    sex: str | None = None
    avatar_color: str = "#6366f1"


class UserCreate(UserBase):
    password: str
    household_id: int


class UserUpdate(BaseModel):
    name: str | None = None
    height_cm: float | None = None
    target_weight_kg: float | None = None
    baseline_activity_level: str | None = None
    birth_date: date | None = None
    sex: str | None = None
    goals_json: list[str] | None = None
    dietary_preferences_json: list[str] | None = None
    dietary_restrictions_json: list[str] | None = None
    disliked_foods_json: list[str] | None = None
    preferred_cuisines_json: list[str] | None = None
    preferred_activities_json: list[str] | None = None
    impossible_activities_json: list[str] | None = None
    disliked_activities_json: list[str] | None = None
    avatar_color: str | None = None


class UserRead(UserBase):
    id: int
    household_id: int
    is_active: bool
    is_admin: bool
    goals_json: list[str] | None = None
    dietary_preferences_json: list[str] | None = None
    dietary_restrictions_json: list[str] | None = None
    disliked_foods_json: list[str] | None = None
    preferred_cuisines_json: list[str] | None = None
    preferred_activities_json: list[str] | None = None
    impossible_activities_json: list[str] | None = None
    disliked_activities_json: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
