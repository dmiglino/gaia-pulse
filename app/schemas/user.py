from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field

# `components/ui.html` rinde este valor dentro de un `style="background-color: …"`.
# El autoescape de Jinja impide salir del atributo (las comillas se vuelven `&#34;`)
# pero **no** impide inyectar propiedades CSS extra, y la app no tiene ninguna CSP
# que sirva de segunda línea. Se valida en la escritura, que es donde se puede: el
# color lo va a elegir el usuario en el perfil a partir de la fase 3.
HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


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

    # El patrón se aplica en la escritura y no en `UserBase`, para que `UserRead` no
    # explote leyendo una fila vieja con un valor fuera de forma.
    avatar_color: HexColor = "#6366f1"


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
    avatar_color: HexColor | None = None


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
