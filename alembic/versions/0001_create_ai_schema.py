"""Create ai schema and ingest_jobs table

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ai schema
    op.execute("CREATE SCHEMA IF NOT EXISTS ai")

    # Create ingest_jobs table
    op.create_table(
        "ingest_jobs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("chunks_count", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="ai",
    )

    op.create_index(
        "idx_ingest_jobs_document_id",
        "ingest_jobs",
        ["document_id"],
        schema="ai",
    )
    op.create_index(
        "idx_ingest_jobs_org_status",
        "ingest_jobs",
        ["organization_id", "status"],
        schema="ai",
    )


def downgrade() -> None:
    op.drop_index("idx_ingest_jobs_org_status", table_name="ingest_jobs", schema="ai")
    op.drop_index("idx_ingest_jobs_document_id", table_name="ingest_jobs", schema="ai")
    op.drop_table("ingest_jobs", schema="ai")
    op.execute("DROP SCHEMA IF EXISTS ai CASCADE")
