"""Add trend agents infrastructure: quality scores, user profiles, interactions, threshold suggestions.

Revision ID: 20251116_trend_agents
Revises: 20251115_enhance_trend_clusters
Create Date: 2025-11-16 10:00:00.000000

Context7: Мультиагентная система для улучшения качества трендов.
- Quality scoring для карточек трендов
- Профилирование интересов пользователей
- Отслеживание взаимодействий
- Предложения по оптимизации порогов
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251116_trend_agents"
down_revision = "20251115_add_trend_cluster_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Расширение инфраструктуры для мультиагентной системы трендов.
    
    Добавляем:
    1. Поля качества в trend_clusters (quality_score, quality_flags, editor_notes, taxonomy_categories)
    2. Таблицу user_trend_profiles для профилирования интересов
    3. Таблицу trend_interactions для отслеживания взаимодействий
    4. Таблицу trend_threshold_suggestions для предложений по порогам
    """
    
    # 1. Расширение trend_clusters
    op.add_column(
        "trend_clusters",
        sa.Column("quality_score", sa.REAL(), nullable=True),
    )
    op.add_column(
        "trend_clusters",
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("editor_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "trend_clusters",
        sa.Column(
            "taxonomy_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "trend_clusters",
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    
    # Context7: Индекс для быстрого поиска по quality_score
    op.create_index(
        "idx_trend_clusters_quality_score",
        "trend_clusters",
        ["quality_score"],
        postgresql_ops={"quality_score": "DESC NULLS LAST"},
    )
    
    # 2. Таблица user_trend_profiles
    op.create_table(
        "user_trend_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "preferred_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ignored_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "preferred_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "typical_time_windows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "interaction_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    
    # Context7: Индексы для user_trend_profiles
    op.create_index(
        "idx_user_trend_profiles_last_updated",
        "user_trend_profiles",
        ["last_updated"],
        postgresql_ops={"last_updated": "DESC"},
    )
    
    # 3. Таблица trend_interactions
    op.create_table(
        "trend_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interaction_type", sa.String(32), nullable=False),  # 'view', 'click_details', 'dismiss', 'save'
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["trend_clusters.id"], ondelete="CASCADE"),
    )
    
    # Context7: Индексы для trend_interactions
    op.create_index(
        "idx_trend_interactions_user_id",
        "trend_interactions",
        ["user_id"],
    )
    op.create_index(
        "idx_trend_interactions_cluster_id",
        "trend_interactions",
        ["cluster_id"],
    )
    op.create_index(
        "idx_trend_interactions_type",
        "trend_interactions",
        ["interaction_type"],
    )
    op.create_index(
        "idx_trend_interactions_created_at",
        "trend_interactions",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "idx_trend_interactions_user_cluster",
        "trend_interactions",
        ["user_id", "cluster_id"],
    )
    
    # 4. Таблица trend_threshold_suggestions
    op.create_table(
        "trend_threshold_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("threshold_name", sa.String(64), nullable=False),  # 'TREND_FREQ_RATIO_THRESHOLD'
        sa.Column("current_value", sa.REAL(), nullable=False),
        sa.Column("suggested_value", sa.REAL(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("analysis_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), server_default="'pending'", nullable=False),  # 'pending', 'accepted', 'rejected'
    )
    
    # Context7: Индексы для trend_threshold_suggestions
    op.create_index(
        "idx_trend_threshold_suggestions_status",
        "trend_threshold_suggestions",
        ["status"],
    )
    op.create_index(
        "idx_trend_threshold_suggestions_created_at",
        "trend_threshold_suggestions",
        ["created_at"],
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "idx_trend_threshold_suggestions_threshold_name",
        "trend_threshold_suggestions",
        ["threshold_name"],
    )


def downgrade() -> None:
    """Откат миграции."""
    
    # Удаление индексов
    op.drop_index("idx_trend_threshold_suggestions_threshold_name", table_name="trend_threshold_suggestions")
    op.drop_index("idx_trend_threshold_suggestions_created_at", table_name="trend_threshold_suggestions")
    op.drop_index("idx_trend_threshold_suggestions_status", table_name="trend_threshold_suggestions")
    op.drop_index("idx_trend_interactions_user_cluster", table_name="trend_interactions")
    op.drop_index("idx_trend_interactions_created_at", table_name="trend_interactions")
    op.drop_index("idx_trend_interactions_type", table_name="trend_interactions")
    op.drop_index("idx_trend_interactions_cluster_id", table_name="trend_interactions")
    op.drop_index("idx_trend_interactions_user_id", table_name="trend_interactions")
    op.drop_index("idx_user_trend_profiles_last_updated", table_name="user_trend_profiles")
    op.drop_index("idx_trend_clusters_quality_score", table_name="trend_clusters")
    
    # Удаление таблиц
    op.drop_table("trend_threshold_suggestions")
    op.drop_table("trend_interactions")
    op.drop_table("user_trend_profiles")
    
    # Удаление колонок из trend_clusters
    op.drop_column("trend_clusters", "last_edited_at")
    op.drop_column("trend_clusters", "taxonomy_categories")
    op.drop_column("trend_clusters", "editor_notes")
    op.drop_column("trend_clusters", "quality_flags")
    op.drop_column("trend_clusters", "quality_score")

