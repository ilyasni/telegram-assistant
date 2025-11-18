"""Add trend_cluster_posts table for sample posts per cluster.

Revision ID: 20251115_add_trend_cluster_posts
Revises: 20251115_add_trend_subscriptions
Create Date: 2025-11-15 18:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251115_add_trend_cluster_posts"
down_revision = "20251115_add_trend_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trend_cluster_posts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("cluster_id", sa.UUID(), nullable=False),
        sa.Column("post_id", sa.UUID(), nullable=False),
        sa.Column("channel_id", sa.UUID(), nullable=True),
        sa.Column("channel_title", sa.Text(), nullable=True),
        sa.Column("content_snippet", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["trend_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "post_id", name="uq_trend_cluster_post"),
    )
    op.create_index(
        "idx_trend_cluster_posts_cluster_time",
        "trend_cluster_posts",
        ["cluster_id", "posted_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_trend_cluster_posts_cluster_time", table_name="trend_cluster_posts")
    op.drop_table("trend_cluster_posts")


