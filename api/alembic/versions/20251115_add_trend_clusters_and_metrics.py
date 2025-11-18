"""add trend_clusters and trend_metrics tables

Revision ID: 20251115_add_trend_clusters
Revises: 20251109_add_user_crawl_triggers
Create Date: 2025-11-15 12:00:00.000000

Context7: хранение embedding-кластеров и baseline метрик для пайплайна трендов.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251115_add_trend_clusters"
down_revision = "20251109_add_user_crawl_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание таблиц trend_clusters и trend_metrics."""
    op.create_table(
        "trend_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="emerging"),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("primary_topic", sa.String(length=255), nullable=True),
        sa.Column("novelty_score", sa.REAL(), nullable=True),
        sa.Column("coherence_score", sa.REAL(), nullable=True),
        sa.Column("source_diversity", sa.Integer(), nullable=True),
        sa.Column("trend_embedding", sa.Text(), nullable=True),
        sa.Column(
            "first_detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_trend_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["resolved_trend_id"], ["trends_detection.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_key", name="uq_trend_clusters_cluster_key"),
    )
    op.create_index("idx_trend_clusters_status", "trend_clusters", ["status"])
    op.create_index(
        "idx_trend_clusters_last_activity",
        "trend_clusters",
        ["last_activity_at"],
        postgresql_ops={"last_activity_at": "DESC"},
    )
    op.create_index(
        "idx_trend_clusters_novelty",
        "trend_clusters",
        ["novelty_score"],
        postgresql_ops={"novelty_score": "DESC NULLS LAST"},
    )

    op.create_table(
        "trend_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("freq_short", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("freq_long", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("freq_baseline", sa.Integer(), nullable=True),
        sa.Column("rate_of_change", sa.REAL(), nullable=True),
        sa.Column("burst_score", sa.REAL(), nullable=True),
        sa.Column("ewm_score", sa.REAL(), nullable=True),
        sa.Column("source_diversity", sa.Integer(), nullable=True),
        sa.Column("coherence_score", sa.REAL(), nullable=True),
        sa.Column("window_short_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("window_long_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "metrics_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["trend_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "metrics_at", name="uq_trend_metrics_cluster_snapshot"),
    )
    op.create_index("idx_trend_metrics_cluster", "trend_metrics", ["cluster_id"])
    op.create_index(
        "idx_trend_metrics_metrics_at",
        "trend_metrics",
        ["metrics_at"],
        postgresql_ops={"metrics_at": "DESC"},
    )


def downgrade() -> None:
    """Удаление таблиц trend_metrics и trend_clusters."""
    op.drop_index("idx_trend_metrics_metrics_at", table_name="trend_metrics")
    op.drop_index("idx_trend_metrics_cluster", table_name="trend_metrics")
    op.drop_table("trend_metrics")

    op.drop_index("idx_trend_clusters_novelty", table_name="trend_clusters")
    op.drop_index("idx_trend_clusters_last_activity", table_name="trend_clusters")
    op.drop_index("idx_trend_clusters_status", table_name="trend_clusters")
    op.drop_table("trend_clusters")


