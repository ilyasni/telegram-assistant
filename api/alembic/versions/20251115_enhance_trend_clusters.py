"""Enhance trend_clusters with time windows and card payload.

Revision ID: 20251115_enhance_trend_clusters
Revises: 20251115_add_trend_clusters
Create Date: 2025-11-15 16:20:00.000000

Context7: тренд-карточка хранит окно, метрики и текстовые TL;DR.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251115_enhance_trend_clusters"
down_revision = "20251115_add_trend_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trend_clusters",
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "trend_clusters",
        sa.Column(
            "window_end",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("window_mentions", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("freq_baseline", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("burst_score", sa.REAL(), nullable=True),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("sources_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("channels_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("why_important", sa.Text(), nullable=True),
    )
    op.add_column(
        "trend_clusters",
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "trend_clusters",
        sa.Column(
            "card_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("trend_clusters", "card_payload")
    op.drop_column("trend_clusters", "topics")
    op.drop_column("trend_clusters", "why_important")
    op.drop_column("trend_clusters", "channels_count")
    op.drop_column("trend_clusters", "sources_count")
    op.drop_column("trend_clusters", "burst_score")
    op.drop_column("trend_clusters", "freq_baseline")
    op.drop_column("trend_clusters", "window_mentions")
    op.drop_column("trend_clusters", "window_end")
    op.drop_column("trend_clusters", "window_start")


