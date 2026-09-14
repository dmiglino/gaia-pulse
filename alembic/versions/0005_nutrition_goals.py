"""per-person declared nutrition goal

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14 00:00:00.000000

`User.goals_json` ya existe y parece el lugar obvio, pero es una lista de etiquetas
libres ("lose weight", "more muscle") que `meal_generator` nunca lee para macros — pisar
su significado para meter un número ahí sería una migración de datos, no una columna
nueva. Esto agrega tres campos numéricos aparte, uno por macro del MVP (proteína, fibra,
calorías), siguiendo el mismo patrón que `height_cm`/`target_weight_kg`: `Numeric` para
los dos que se declaran con decimales, y sin tocar `goals_json`.

Nullable los tres, porque no declarar un objetivo es el caso normal y no un dato que
falta: sin él, la tarjeta de macros sigue comparando contra el propio promedio, como
hacía antes de esta fase.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("goal_protein_g", sa.Numeric(6, 2), nullable=True))
    op.add_column("users", sa.Column("goal_fiber_g", sa.Numeric(6, 2), nullable=True))
    op.add_column("users", sa.Column("goal_calories_kcal", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "goal_calories_kcal")
    op.drop_column("users", "goal_fiber_g")
    op.drop_column("users", "goal_protein_g")
