"""Create initial orchestrator schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2024-10-10 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )

    op.create_table(
        "encoding_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("name", name="uq_encoding_profiles_name"),
    )

    op.create_table(
        "library_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("library", sa.String(length=100), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("profile", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.UniqueConstraint("path", name="uq_library_entries_path"),
    )
    op.create_index("idx_library_entries_library", "library_entries", ["library"], unique=False)

    op.create_table(
        "job_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("library", sa.String(length=100), nullable=False),
        sa.Column("profile", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", name="uq_job_history_job_id"),
    )
    op.create_index(
        "idx_job_history_library_status", "job_history", ["library", "status"], unique=False
    )
    op.create_index("idx_job_history_path", "job_history", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_job_history_path", table_name="job_history")
    op.drop_index("idx_job_history_library_status", table_name="job_history")
    op.drop_table("job_history")

    op.drop_index("idx_library_entries_library", table_name="library_entries")
    op.drop_table("library_entries")

    op.drop_table("encoding_profiles")
    op.drop_table("config")
