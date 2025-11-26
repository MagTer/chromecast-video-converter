"""Add AQ strength column.

Revision ID: 0004_aq_strength_column
Revises: 0003_nvenc_profile_extensions
Create Date: 2025-11-25 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_aq_strength_column"
down_revision = "0003_nvenc_profile_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "encoding_profiles",
        sa.Column("aq_strength", sa.Integer(), nullable=False, server_default="7"),
    )
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE encoding_profiles SET aq_strength = 7 WHERE aq_strength IS NULL"))

    if conn.dialect.name == "sqlite":
        # SQLite cannot drop a column default in-place, so recreate the table via
        # batch_alter_table to align the new schema with other backends.
        with op.batch_alter_table("encoding_profiles", recreate="always") as batch_op:
            batch_op.alter_column(
                "aq_strength",
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default=None,
            )
    else:
        op.alter_column("encoding_profiles", "aq_strength", server_default=None)


def downgrade() -> None:
    op.drop_column("encoding_profiles", "aq_strength")
