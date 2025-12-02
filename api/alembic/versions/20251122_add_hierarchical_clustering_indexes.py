"""add hierarchical clustering columns and indexes to trend_clusters

Revision ID: 20251122_hierarchical_indexes
Revises: 20251120_add_is_generic
Create Date: 2025-11-22 12:00:00.000000

Context7: Добавление колонок (parent_cluster_id, cluster_level) и индексов для иерархической кластеризации.
Соответствует SQL миграции supabase/migrations/20251122_add_hierarchical_clustering_to_trend_clusters.sql
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251122_hierarchical_indexes"
down_revision = "20251120_add_is_generic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление колонок и индексов для иерархической кластеризации."""
    # Context7: Проверяем существование колонок, так как SQL миграция Supabase могла уже добавить их
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('trend_clusters')]
    
    # Add parent_cluster_id column if it doesn't exist
    if 'parent_cluster_id' not in columns:
        op.add_column(
            "trend_clusters",
            sa.Column("parent_cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        # Add foreign key constraint
        op.create_foreign_key(
            "fk_trend_clusters_parent_cluster_id",
            "trend_clusters",
            "trend_clusters",
            ["parent_cluster_id"],
            ["id"],
            ondelete="SET NULL"
        )
    
    # Add cluster_level column if it doesn't exist
    if 'cluster_level' not in columns:
        op.add_column(
            "trend_clusters",
            sa.Column("cluster_level", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )
    
    # Context7: Проверяем существование индексов, так как SQL миграция Supabase могла уже создать их
    indexes = [idx['name'] for idx in inspector.get_indexes('trend_clusters')]
    
    # Index for finding sub-clusters of a parent
    if 'idx_trend_clusters_parent' not in indexes:
        op.create_index(
            "idx_trend_clusters_parent",
            "trend_clusters",
            ["parent_cluster_id"],
            postgresql_where=sa.text("parent_cluster_id IS NOT NULL"),
        )
    
    # Index for filtering by cluster level
    if 'idx_trend_clusters_level' not in indexes:
        op.create_index(
            "idx_trend_clusters_level",
            "trend_clusters",
            ["cluster_level"],
            postgresql_where=sa.text("cluster_level > 1"),
        )
    
    # Composite index for finding sub-clusters efficiently
    if 'idx_trend_clusters_parent_level' not in indexes:
        op.create_index(
            "idx_trend_clusters_parent_level",
            "trend_clusters",
            ["parent_cluster_id", "cluster_level"],
            postgresql_where=sa.text("parent_cluster_id IS NOT NULL"),
        )


def downgrade() -> None:
    """Удаление индексов и колонок для иерархической кластеризации."""
    op.drop_index("idx_trend_clusters_parent_level", table_name="trend_clusters")
    op.drop_index("idx_trend_clusters_level", table_name="trend_clusters")
    op.drop_index("idx_trend_clusters_parent", table_name="trend_clusters")
    
    # Drop foreign key constraint before dropping column
    op.drop_constraint("fk_trend_clusters_parent_cluster_id", "trend_clusters", type_="foreignkey")
    op.drop_column("trend_clusters", "cluster_level")
    op.drop_column("trend_clusters", "parent_cluster_id")

