"""add group discovery tables

Revision ID: 20251109_add_group_discovery
Revises: 20251109_add_group_digest
Create Date: 2025-11-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251109_add_group_discovery"
down_revision = "20251109_add_group_digest"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_discovery_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("connected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_group_discovery_requests_tenant",
        "group_discovery_requests",
        ["tenant_id"],
    )
    op.create_index(
        "idx_group_discovery_requests_status",
        "group_discovery_requests",
        ["status"],
    )
    op.create_index(
        "idx_group_discovery_requests_created_at",
        "group_discovery_requests",
        ["created_at"],
    )


def downgrade():
    op.drop_index("idx_group_discovery_requests_tenant", table_name="group_discovery_requests")
    op.drop_index("idx_group_discovery_requests_created_at", table_name="group_discovery_requests")
    op.drop_index("idx_group_discovery_requests_status", table_name="group_discovery_requests")
    op.drop_table("group_discovery_requests")

