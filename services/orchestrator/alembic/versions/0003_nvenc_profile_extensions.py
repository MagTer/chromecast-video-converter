"""Add advanced NVENC profile controls.

Revision ID: 0003_nvenc_profile_extensions
Revises: 0002_schema_alignment
Create Date: 2025-11-24 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_nvenc_profile_extensions"
down_revision = "0002_schema_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "encoding_profiles",
        sa.Column("bitrate", sa.String(), nullable=False, server_default="8M"),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column("bframes", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column("lookahead", sa.Integer(), nullable=False, server_default="24"),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column(
            "adaptive_b_frames",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column("aq", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column("spatial_aq", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "encoding_profiles",
        sa.Column("temporal_aq", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    conn = op.get_bind()
    columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info('encoding_profiles')"))}
    if "max_bitrate" in columns:
        conn.execute(
            sa.text(
                "UPDATE encoding_profiles SET bitrate = max_bitrate "
                "WHERE bitrate IS NULL OR bitrate = ''"
            )
        )


def downgrade() -> None:
    op.drop_column("encoding_profiles", "temporal_aq")
    op.drop_column("encoding_profiles", "spatial_aq")
    op.drop_column("encoding_profiles", "aq")
    op.drop_column("encoding_profiles", "adaptive_b_frames")
    op.drop_column("encoding_profiles", "lookahead")
    op.drop_column("encoding_profiles", "bframes")
    op.drop_column("encoding_profiles", "bitrate")
