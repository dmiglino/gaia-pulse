"""subject anchoring and time-bounded snooze for suggestions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13 00:00:00.000000

La única migración de toda la v3, y por eso trae las tres columnas de la fase 4.4 juntas
en vez de una revisión por sub-commit.

`subject_type` / `subject_name` son el sujeto de la sugerencia: de qué habla, en lugar de
cómo quedó redactada. Hasta acá el feedback se guardaba contra
`suggestion.title.lower()[:200]`, así que rechazar *"Time to get moving!"* enseñaba sobre
esa frase: el scorer la comparaba por bolsa de palabras contra el título, texto y
racional de cada candidato futuro, y con 30% de solape alcanzaba para penalizar — o 60%
para **filtrar** — a cualquier candidato que compartiera palabras comunes, cruzando
categorías. Con el sujeto, rechazar el brócoli enseña sobre el brócoli.

Las dos son nullable: las filas que ya existen no tienen sujeto y no se puede inventar
uno a partir del título — inventarlo es el bug. Se quedan sin enseñar nada, que es lo
correcto, y se van con el tiempo.

`snoozed_until` es de la 4.4.7: hoy `snoozed` graba una señal de valor 0.0 que no entra
ni en la lista positiva ni en la negativa —escrita y jamás leída—, y "posponer" termina
actuando como rechazo porque comparte el `signal_type` con `dismissed`. La columna existe
en `notifications` desde la `0001` y **no** en `suggestions`, que es donde hace falta.

Sin índice nuevo a propósito: la consulta que se agrega —las sugerencias pendientes de
una persona con este sujeto— ya entra por el índice de `status`, y en una casa de dos
personas la tabla se cuenta en cientos de filas. Un índice compuesto acá sería peso sin
lectura que lo pague (regla 6 de `AGENTS.md`).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("suggestions", sa.Column("subject_type", sa.String(40), nullable=True))
    op.add_column("suggestions", sa.Column("subject_name", sa.String(200), nullable=True))
    op.add_column(
        "suggestions",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("suggestions", "snoozed_until")
    op.drop_column("suggestions", "subject_name")
    op.drop_column("suggestions", "subject_type")
