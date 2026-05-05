from datetime import datetime

from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: int
    household_id: int | None
    user_id: int | None
    category: str
    title: str
    body: str
    priority: int
    source_type: str
    created_at: datetime
    read_at: datetime | None
    dismissed_at: datetime | None
    snoozed_until: datetime | None
    is_read: bool
    is_dismissed: bool

    model_config = {"from_attributes": True}


class NotificationCreate(BaseModel):
    household_id: int | None = None
    user_id: int | None = None
    category: str
    title: str
    body: str
    priority: int = 5
    source_type: str = "system"
    related_entity_type: str | None = None
    related_entity_id: int | None = None
