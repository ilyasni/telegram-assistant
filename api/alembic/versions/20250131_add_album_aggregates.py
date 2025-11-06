"""add album aggregates to post_enrichment

Revision ID: 20250131_album_aggregates
Revises: 20250131_engagement_metrics
Create Date: 2025-01-31 12:30:00.000000

Context7: Добавляем агрегаты альбомов в post_enrichment для хранения информации об альбомах:
- album_size - количество элементов в альбоме
- vision_labels_agg - агрегированные метки vision из всех элементов альбома
- ocr_present - наличие OCR текста в альбоме
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250131_album_aggregates'
down_revision = '20250131_engagement_metrics'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Добавление агрегатов альбомов в post_enrichment.
    
    Шаги:
    1. Добавляем колонки album_size, vision_labels_agg, ocr_present
    2. Добавляем GIN индекс на vision_labels_agg для быстрого поиска
    """
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('post_enrichment')]
    
    # Добавляем album_size (количество элементов в альбоме)
    if 'album_size' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('album_size', sa.Integer(), nullable=True))
    
    # Добавляем vision_labels_agg (агрегированные метки vision из всех элементов альбома)
    if 'vision_labels_agg' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('vision_labels_agg', postgresql.JSONB(), nullable=True))
    
    # Добавляем ocr_present (наличие OCR текста в альбоме)
    if 'ocr_present' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('ocr_present', sa.Boolean(), nullable=True, server_default='false'))
        op.alter_column('post_enrichment', 'ocr_present', nullable=False, server_default='false')
    
    # Проверяем существование индексов
    result = conn.execute(sa.text("""
        SELECT indexname FROM pg_indexes 
        WHERE schemaname = 'public' AND tablename = 'post_enrichment'
    """))
    existing_index_names = [row[0] for row in result]
    
    # GIN индекс на vision_labels_agg для быстрого поиска по агрегированным меткам
    if 'idx_post_enrichment_vision_labels_agg_gin' not in existing_index_names:
        op.execute("""
            CREATE INDEX idx_post_enrichment_vision_labels_agg_gin 
            ON post_enrichment USING GIN (vision_labels_agg)
            WHERE vision_labels_agg IS NOT NULL
        """)


def downgrade() -> None:
    """
    Откат миграции: удаление агрегатов альбомов из post_enrichment.
    """
    
    # Удаляем индекс
    op.execute("DROP INDEX IF EXISTS idx_post_enrichment_vision_labels_agg_gin")
    
    # Удаляем колонки
    op.drop_column('post_enrichment', 'ocr_present')
    op.drop_column('post_enrichment', 'vision_labels_agg')
    op.drop_column('post_enrichment', 'album_size')

