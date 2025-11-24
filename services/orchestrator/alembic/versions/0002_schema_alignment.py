"""Align library schema with application models.

Revision ID: 0002_schema_alignment
Revises: 0001_initial_schema
Create Date: 2024-10-20 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_schema_alignment"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "libraries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("root", sa.String(), nullable=False),
        sa.Column("depth", sa.String(), nullable=False, server_default="max"),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["encoding_profiles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("name", name="uq_libraries_name"),
    )

    op.create_index("idx_libraries_profile", "libraries", ["profile_id"], unique=False)

    op.rename_table("library_entries", "library_entries_old")

    op.create_table(
        "library_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(), nullable=False, unique=True),
        sa.Column("library", sa.String(length=100), nullable=False),
        sa.Column("profile", sa.String(length=100), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("output_path", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_job_id", sa.String(), nullable=True),
        sa.Column("original_missing", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["encoding_profiles.id"], ondelete="RESTRICT"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO library_entries (
                id, path, library, profile, profile_id, status, output_path,
                last_error, last_job_id, original_missing, created_at, updated_at
            )
            SELECT
                id,
                path,
                library,
                profile,
                NULL AS profile_id,
                status,
                NULL AS output_path,
                message AS last_error,
                NULL AS last_job_id,
                0 AS original_missing,
                last_seen_at AS created_at,
                updated_at
            FROM library_entries_old
            """
        )
    )

    op.drop_table("library_entries_old")

    op.create_index("idx_library_entries_library", "library_entries", ["library"], unique=False)
    op.create_index("idx_library_entries_profile", "library_entries", ["profile_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_library_entries_profile", table_name="library_entries")
    op.drop_index("idx_library_entries_library", table_name="library_entries")

    op.rename_table("library_entries", "library_entries_new")

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

    op.execute(
        sa.text(
            """
            INSERT INTO library_entries (
                id, library, path, profile, status, message, last_seen_at, updated_at
            )
            SELECT
                id,
                library,
                path,
                profile,
                status,
                last_error,
                created_at,
                updated_at
            FROM library_entries_new
            """
        )
    )

    op.drop_table("library_entries_new")

    op.create_index("idx_library_entries_library", "library_entries", ["library"], unique=False)

    op.drop_index("idx_libraries_profile", table_name="libraries")
    op.drop_table("libraries")
