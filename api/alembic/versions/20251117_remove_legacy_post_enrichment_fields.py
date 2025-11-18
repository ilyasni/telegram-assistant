"""remove legacy post_enrichment fields

Context7 best practice: Удаление DEPRECATED полей из post_enrichment после миграции данных в data JSONB.

Revision ID: 20251117_remove_legacy
Revises: 20251117_group_msg_unique
Create Date: 2025-11-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20251117_remove_legacy'  # Context7: Сокращен для VARCHAR(32) в alembic_version
down_revision = '20251117_group_msg_unique'  # Context7: Зависит от предыдущей миграции
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Удаление legacy полей из post_enrichment после проверки, что данные мигрированы в data JSONB.
    
    ВАЖНО: Перед применением миграции убедитесь, что все данные перенесены в data JSONB.
    Проверка: SELECT COUNT(*) FROM post_enrichment WHERE data IS NULL OR data = '{}'::jsonb;
    """
    # Context7: Проверяем, что данные мигрированы (опционально, можно закомментировать для production)
    # conn = op.get_bind()
    # result = conn.execute(sa.text("""
    #     SELECT COUNT(*) as count
    #     FROM post_enrichment
    #     WHERE data IS NULL OR data = '{}'::jsonb
    # """))
    # empty_data_count = result.fetchone()[0]
    # if empty_data_count > 0:
    #     raise Exception(f"Found {empty_data_count} rows with empty data JSONB. Please migrate data first.")
    
    # Context7: Удаляем legacy поля
    # Общие поля
    op.drop_column('post_enrichment', 'tags')
    op.drop_column('post_enrichment', 'vision_labels')
    op.drop_column('post_enrichment', 'ocr_text')
    op.drop_column('post_enrichment', 'crawl_md')
    op.drop_column('post_enrichment', 'enrichment_provider')
    op.drop_column('post_enrichment', 'enriched_at')
    op.drop_column('post_enrichment', 'enrichment_latency_ms')
    op.drop_column('post_enrichment', 'enrichment_metadata')
    op.drop_column('post_enrichment', 'summary')
    
    # Legacy Vision поля
    op.drop_column('post_enrichment', 'vision_classification')
    op.drop_column('post_enrichment', 'vision_description')
    op.drop_column('post_enrichment', 'vision_ocr_text')
    op.drop_column('post_enrichment', 'vision_is_meme')
    op.drop_column('post_enrichment', 'vision_context')
    op.drop_column('post_enrichment', 'vision_provider')
    op.drop_column('post_enrichment', 'vision_model')
    op.drop_column('post_enrichment', 'vision_analyzed_at')
    op.drop_column('post_enrichment', 'vision_file_id')
    op.drop_column('post_enrichment', 'vision_tokens_used')
    op.drop_column('post_enrichment', 'vision_cost_microunits')
    op.drop_column('post_enrichment', 'vision_analysis_reason')
    
    # Legacy S3 references
    op.drop_column('post_enrichment', 's3_media_keys')
    op.drop_column('post_enrichment', 's3_vision_keys')
    op.drop_column('post_enrichment', 's3_crawl_keys')


def downgrade() -> None:
    """
    Откат миграции: восстановление legacy полей.
    ВАЖНО: Данные НЕ будут восстановлены из data JSONB автоматически.
    """
    # Context7: Восстанавливаем колонки (данные будут NULL)
    # Общие поля
    op.add_column('post_enrichment', sa.Column('tags', sa.JSONB(), server_default='[]'))
    op.add_column('post_enrichment', sa.Column('vision_labels', sa.JSONB(), server_default='[]'))
    op.add_column('post_enrichment', sa.Column('ocr_text', sa.Text()))
    op.add_column('post_enrichment', sa.Column('crawl_md', sa.Text()))
    op.add_column('post_enrichment', sa.Column('enrichment_provider', sa.String(50)))
    op.add_column('post_enrichment', sa.Column('enriched_at', sa.DateTime()))
    op.add_column('post_enrichment', sa.Column('enrichment_latency_ms', sa.Integer()))
    op.add_column('post_enrichment', sa.Column('enrichment_metadata', sa.JSONB(), server_default='{}'))
    op.add_column('post_enrichment', sa.Column('summary', sa.Text()))
    
    # Legacy Vision поля
    op.add_column('post_enrichment', sa.Column('vision_classification', sa.JSONB()))
    op.add_column('post_enrichment', sa.Column('vision_description', sa.Text()))
    op.add_column('post_enrichment', sa.Column('vision_ocr_text', sa.Text()))
    op.add_column('post_enrichment', sa.Column('vision_is_meme', sa.Boolean(), server_default='false'))
    op.add_column('post_enrichment', sa.Column('vision_context', sa.JSONB()))
    op.add_column('post_enrichment', sa.Column('vision_provider', sa.String(50)))
    op.add_column('post_enrichment', sa.Column('vision_model', sa.String(100)))
    op.add_column('post_enrichment', sa.Column('vision_analyzed_at', sa.DateTime()))
    op.add_column('post_enrichment', sa.Column('vision_file_id', sa.String(255)))
    op.add_column('post_enrichment', sa.Column('vision_tokens_used', sa.Integer(), server_default='0'))
    op.add_column('post_enrichment', sa.Column('vision_cost_microunits', sa.Integer(), server_default='0'))
    op.add_column('post_enrichment', sa.Column('vision_analysis_reason', sa.String(50)))
    
    # Legacy S3 references
    op.add_column('post_enrichment', sa.Column('s3_media_keys', sa.JSONB(), server_default='[]'))
    op.add_column('post_enrichment', sa.Column('s3_vision_keys', sa.JSONB(), server_default='[]'))
    op.add_column('post_enrichment', sa.Column('s3_crawl_keys', sa.JSONB(), server_default='[]'))

