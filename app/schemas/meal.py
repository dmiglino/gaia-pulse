from datetime import datetime

from pydantic import BaseModel


class MealItemCreate(BaseModel):
    food_name: str
    quantity: float | None = None
    unit: str | None = None
    estimated_grams: float | None = None
    preparation: str | None = None
    affects_stock: bool = False


class MealParticipantCreate(BaseModel):
    user_id: int
    items: list[MealItemCreate]
    portion_label: str | None = None
    estimated_total_grams: float | None = None
    hunger_before: int | None = None
    satiety_after: int | None = None
    notes: str | None = None


class MealEventCreate(BaseModel):
    timestamp: datetime
    meal_type: str = "other"  # breakfast/lunch/snack/dinner/other
    context: str = "home"
    participants: list[MealParticipantCreate]
    notes: str | None = None


class MealItemRead(BaseModel):
    id: int
    normalized_free_text_name: str
    quantity: float | None
    unit: str | None
    estimated_grams: float | None
    preparation: str | None
    affects_stock: bool

    model_config = {"from_attributes": True}


class MealParticipantRead(BaseModel):
    id: int
    user_id: int
    portion_label: str | None
    estimated_total_grams: float | None
    hunger_before: int | None
    satiety_after: int | None
    notes: str | None
    items_consumed: list[MealItemRead]

    model_config = {"from_attributes": True}


class MealEventRead(BaseModel):
    id: int
    household_id: int
    timestamp: datetime
    meal_type: str
    context: str
    notes: str | None
    participants: list[MealParticipantRead]
    created_at: datetime

    model_config = {"from_attributes": True}
