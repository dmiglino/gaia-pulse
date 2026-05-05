"""onboarding flag and blood_analyses table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add onboarding_completed to users
    op.add_column(
        "users",
        sa.Column(
            "onboarding_completed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Create blood_analyses table
    op.create_table(
        "blood_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("analysis_date", sa.Date(), nullable=True),
        sa.Column("lab_name", sa.String(200), nullable=True),
        sa.Column("file_name", sa.String(300), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("values_json", postgresql.JSONB(), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("parsing_method", sa.String(20), nullable=True),
        sa.Column(
            "status",
            sa.String(30),
            nullable=False,
            server_default="analyzed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blood_analyses_user_id", "blood_analyses", ["user_id"])
    op.create_index("ix_blood_analyses_analysis_date", "blood_analyses", ["analysis_date"])
    op.create_index("ix_blood_analyses_status", "blood_analyses", ["status"])
    op.create_index("ix_blood_analyses_created_at", "blood_analyses", ["created_at"])


def downgrade() -> None:
    op.drop_table("blood_analyses")
    op.drop_column("users", "onboarding_completed")
