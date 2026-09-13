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
    #: El sujeto del aviso. Las columnas existen desde el principio y desde la 4.2 los
    #: jobs las llenan, pero este schema no las exponía: un cliente de `/api/v1` recibía
    #: "no queda leche" sin nada que identifique *qué* leche, o sea sin poder ofrecer la
    #: acción que la pantalla web ya ofrece. Ver `app/web/actions.py`.
    related_entity_type: str | None
    related_entity_id: int | None
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
