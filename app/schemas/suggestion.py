from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SuggestionRead(BaseModel):
    id: int
    scope_type: str
    household_id: int | None
    scope_user_id: int | None
    category: str
    title: str
    text: str
    rationale: str
    evidence_summary: str | None
    priority: int
    confidence: float
    source_type: str
    status: str
    feedback_notes: str | None
    created_at: datetime
    responded_at: datetime | None

    model_config = {"from_attributes": True}


class SuggestionFeedback(BaseModel):
    status: Literal["accepted", "rejected", "snoozed", "dismissed"]
    feedback_notes: str | None = None


PreferenceSignal = Literal[
    "likes", "dislikes", "impossible", "possible_sometimes", "avoid", "preferred"
]


class RecommendationPreferenceCreate(BaseModel):
    item_type: str = Field(..., max_length=40)
    item_name: str = Field(..., min_length=1, max_length=200)
    preference_signal: PreferenceSignal
    strength: float = Field(1.0, ge=0.0, le=1.0)
    notes: str | None = None

    @field_validator("item_name")
    @classmethod
    def strip_item_name(cls, v: str) -> str:
        return v.strip()


class RecommendationPreferenceRead(BaseModel):
    id: int
    user_id: int
    item_type: str
    item_name: str
    preference_signal: str
    strength: float
    notes: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}
