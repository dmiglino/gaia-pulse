from datetime import datetime

from pydantic import BaseModel, field_validator


class FoodItemRead(BaseModel):
    id: int
    canonical_name: str
    category: str | None = None
    base_unit: str
    calories_per_100g: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    perishable: bool

    model_config = {"from_attributes": True}


class PantryStockRead(BaseModel):
    id: int
    household_id: int
    food_item_id: int
    current_quantity: float
    unit: str
    low_stock_threshold: float | None = None
    storage_location: str | None = None
    updated_at: datetime
    food_item: FoodItemRead
    is_low: bool

    model_config = {"from_attributes": True}


class PantryStockUpdate(BaseModel):
    current_quantity: float
    unit: str | None = None
    low_stock_threshold: float | None = None
    storage_location: str | None = None


class PantryMovementRead(BaseModel):
    id: int
    household_id: int
    user_id: int | None
    food_item_id: int
    movement_type: str
    quantity: float
    unit: str
    timestamp: datetime
    notes: str | None = None
    food_item: FoodItemRead

    model_config = {"from_attributes": True}


class PurchaseItem(BaseModel):
    food_name: str
    quantity: float
    unit: str = "unit"

    @field_validator("food_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip().lower()


class PurchaseRequest(BaseModel):
    items: list[PurchaseItem]
    notes: str | None = None


class StockAdjustRequest(BaseModel):
    food_name: str
    quantity: float
    unit: str
    movement_type: str = "adjustment"  # purchase/consumption/adjustment/discard
    notes: str | None = None
