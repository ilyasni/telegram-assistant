"""Create chat trend subscriptions table.

Revision ID: 20251115_add_trend_subscriptions
Revises: 20251115_enhance_trend_clusters
Create Date: 2025-11-15 17:05:00.000000

Context7: подписки для авто-дайджестов трендов.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251115_add_trend_subscriptions"
down_revision = "20251115_enhance_trend_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_trend_subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "frequency", name="uq_trend_subscription_chat_frequency"),
    )
    op.create_index(
        "idx_trend_subscription_active",
        "chat_trend_subscriptions",
        ["is_active"],
    )
    op.create_index(
        "idx_trend_subscription_last_sent",
        "chat_trend_subscriptions",
        ["last_sent_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_trend_subscription_last_sent", table_name="chat_trend_subscriptions")
    op.drop_index("idx_trend_subscription_active", table_name="chat_trend_subscriptions")
    op.drop_table("chat_trend_subscriptions")


