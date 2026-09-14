"""spanish aliases for the exercise catalog

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14 00:00:00.000000

`exercise_types` se siembra en inglés ("Bench Press", "Cycling") y hasta acá no tenía
adónde poner la forma castellana con la que la casa realmente lo escribe ("press de
banca", "andar en bici"). `FoodItem.aliases_json` resuelve exactamente este problema del
lado de la comida desde la `0001`; esta migración le da a `exercise_types` la misma
columna, con la misma forma (`["tomato", "tomate"]` allá, `["press de banca"]` acá).

Nullable porque no hay fila sin alias que perder: `seed.py` la llena para las 20 filas que
siembra, pero una fila de catálogo agregada a mano sin alias sigue siendo válida — sin
alias, sigue resolviendo por su nombre canónico, igual que un alimento sin ellos.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exercise_types",
        sa.Column("aliases_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exercise_types", "aliases_json")
