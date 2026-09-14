"""normalize blood markers into their own table

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14 00:00:00.000000

`BloodAnalysis.values_json` guardaba los marcadores de un panel como un blob
(`{"ferritin": {"value": 12, "unit": "ng/mL", ...}}`). Comparar el mismo marcador entre
dos paneles significaba traer los dos blobs completos y comparar a mano en Python — no
hay tendencia por SQL sobre una clave adentro de un JSON. Esta migración saca cada
marcador a su propia fila en `blood_markers` (`analysis_id`, `marker_key`, `value`, ...),
migra los paneles ya cargados, y borra la columna vieja: no quedan dos formas de leer lo
mismo.

`analysis_id` + `marker_key` es único: un panel no puede tener el mismo marcador dos
veces, que es justo la forma que ya tenía el `dict` que reemplaza.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_blood_analyses = sa.table(
    "blood_analyses",
    sa.column("id", sa.Integer),
    sa.column("values_json", postgresql.JSONB),
)
_blood_markers = sa.table(
    "blood_markers",
    sa.column("id", sa.Integer),
    sa.column("analysis_id", sa.Integer),
    sa.column("marker_key", sa.String),
    sa.column("value", sa.Numeric),
    sa.column("unit", sa.String),
    sa.column("ref_min", sa.Numeric),
    sa.column("ref_max", sa.Numeric),
    sa.column("status", sa.String),
    sa.column("display_name", sa.String),
    sa.column("category", sa.String),
)


def upgrade() -> None:
    op.create_table(
        "blood_markers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("marker_key", sa.String(50), nullable=False),
        sa.Column("value", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("ref_min", sa.Numeric(10, 3), nullable=True),
        sa.Column("ref_max", sa.Numeric(10, 3), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(30), nullable=False, server_default="other"),
        sa.ForeignKeyConstraint(["analysis_id"], ["blood_analyses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id", "marker_key", name="uq_blood_markers_analysis_marker"),
    )
    op.create_index("ix_blood_markers_analysis_id", "blood_markers", ["analysis_id"])
    op.create_index("ix_blood_markers_marker_key", "blood_markers", ["marker_key"])

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_blood_analyses.c.id, _blood_analyses.c.values_json)
    ).fetchall()
    for analysis_id, values in rows:
        if not values:
            continue
        for marker_key, data in values.items():
            if not isinstance(data, dict) or data.get("value") is None:
                continue
            bind.execute(
                _blood_markers.insert().values(
                    analysis_id=analysis_id,
                    marker_key=marker_key,
                    value=data["value"],
                    unit=data.get("unit"),
                    ref_min=data.get("ref_min"),
                    ref_max=data.get("ref_max"),
                    status=data.get("status") or "unknown",
                    display_name=data.get("display_name") or marker_key.replace("_", " ").title(),
                    category=data.get("category") or "other",
                )
            )

    op.drop_column("blood_analyses", "values_json")


def downgrade() -> None:
    op.add_column("blood_analyses", sa.Column("values_json", postgresql.JSONB(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            _blood_markers.c.analysis_id,
            _blood_markers.c.marker_key,
            _blood_markers.c.value,
            _blood_markers.c.unit,
            _blood_markers.c.ref_min,
            _blood_markers.c.ref_max,
            _blood_markers.c.status,
            _blood_markers.c.display_name,
            _blood_markers.c.category,
        )
    ).fetchall()

    grouped: dict[int, dict[str, dict]] = {}
    for analysis_id, key, value, unit, ref_min, ref_max, status, display_name, category in rows:
        grouped.setdefault(analysis_id, {})[key] = {
            "value": float(value),
            "unit": unit,
            "ref_min": float(ref_min) if ref_min is not None else None,
            "ref_max": float(ref_max) if ref_max is not None else None,
            "status": status,
            "display_name": display_name,
            "category": category,
        }
    for analysis_id, values in grouped.items():
        bind.execute(
            _blood_analyses.update()
            .where(_blood_analyses.c.id == analysis_id)
            .values(values_json=values)
        )

    op.drop_table("blood_markers")
