"""add media registry and vision fields

Revision ID: 20250128_media_vision
Revises: 012781057884
Create Date: 2025-01-28 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250128_media_vision'
down_revision = '012781057884'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================================
    # MEDIA OBJECTS TABLE (Content-addressed storage registry)
    # ============================================================================
    op.create_table(
        'media_objects',
        sa.Column('file_sha256', sa.String(64), nullable=False, primary_key=True),
        sa.Column('mime', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('s3_key', sa.Text(), nullable=False),
        sa.Column('s3_bucket', sa.Text(), nullable=False, server_default='test-467940'),
        sa.Column('first_seen_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('last_seen_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.Column('refs_count', sa.Integer(), server_default='0'),
        
        sa.CheckConstraint(
            'size_bytes >= 0 AND size_bytes <= 41943040',
            name='chk_media_size_bytes'
        ),
        sa.CheckConstraint(
            "mime ~ '^(image|video|application)/'",
            name='chk_media_mime'
        ),
    )
    
    op.create_index('idx_media_mime', 'media_objects', ['mime'])
    op.create_index('idx_media_size', 'media_objects', ['size_bytes'])
    op.create_index('idx_media_refs', 'media_objects', ['refs_count'])
    op.create_index('idx_media_last_seen', 'media_objects', ['last_seen_at'])
    
    # ============================================================================
    # POST MEDIA MAP TABLE (Many-to-many: posts ↔ media)
    # ============================================================================
    op.create_table(
        'post_media_map',
        sa.Column('post_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_sha256', sa.String(64), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0'),
        sa.Column('role', sa.String(50), server_default='primary'),
        sa.Column('uploaded_at', sa.DateTime(), server_default=sa.text('now()')),
        
        sa.ForeignKeyConstraint(['post_id'], ['posts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['file_sha256'], ['media_objects.file_sha256']),
        sa.PrimaryKeyConstraint('post_id', 'file_sha256'),
        
        sa.CheckConstraint(
            "role IN ('primary', 'attachment', 'thumbnail')",
            name='chk_pmm_role'
        ),
    )
    
    op.create_index('idx_pmm_sha', 'post_media_map', ['file_sha256'])
    op.create_index('idx_pmm_post', 'post_media_map', ['post_id'])
    
    # ============================================================================
    # POST ENRICHMENT VISION FIELDS
    # ============================================================================
    
    # Vision analysis fields
    op.add_column('post_enrichment', sa.Column('summary', sa.Text()))
    op.add_column('post_enrichment', sa.Column('vision_classification', postgresql.JSONB))
    op.add_column('post_enrichment', sa.Column('vision_description', sa.Text()))
    op.add_column('post_enrichment', sa.Column('vision_ocr_text', sa.Text()))
    op.add_column('post_enrichment', sa.Column('vision_is_meme', sa.Boolean(), server_default='false'))
    op.add_column('post_enrichment', sa.Column('vision_context', postgresql.JSONB))
    op.add_column('post_enrichment', sa.Column('vision_provider', sa.String(50)))
    op.add_column('post_enrichment', sa.Column('vision_model', sa.String(100)))
    op.add_column('post_enrichment', sa.Column('vision_analyzed_at', sa.DateTime()))
    op.add_column('post_enrichment', sa.Column('vision_file_id', sa.String(255)))
    op.add_column('post_enrichment', sa.Column('vision_tokens_used', sa.Integer(), server_default='0'))
    op.add_column('post_enrichment', sa.Column('vision_cost_microunits', sa.Integer(), server_default='0'))
    op.add_column('post_enrichment', sa.Column('vision_analysis_reason', sa.String(50)))
    
    # S3 storage references
    op.add_column('post_enrichment', sa.Column('s3_media_keys', postgresql.JSONB, server_default='[]'))
    op.add_column('post_enrichment', sa.Column('s3_vision_keys', postgresql.JSONB, server_default='[]'))
    op.add_column('post_enrichment', sa.Column('s3_crawl_keys', postgresql.JSONB, server_default='[]'))
    
    # Constraints
    op.create_check_constraint(
        'chk_vision_provider',
        'post_enrichment',
        "vision_provider IS NULL OR vision_provider IN ('gigachat', 'ocr_fallback', 'none')"
    )
    
    op.create_check_constraint(
        'chk_vision_analysis_reason',
        'post_enrichment',
        "vision_analysis_reason IS NULL OR vision_analysis_reason IN ('new', 'retry', 'cache_hit', 'fallback', 'skipped')"
    )
    
    op.create_check_constraint(
        'chk_vision_tokens_used',
        'post_enrichment',
        'vision_tokens_used >= 0'
    )
    
    # GIN indexes для JSONB полей
    op.execute("CREATE INDEX idx_pe_vision_class ON post_enrichment USING GIN (vision_classification)")
    op.execute("CREATE INDEX idx_pe_vision_ctx ON post_enrichment USING GIN (vision_context)")
    op.execute("CREATE INDEX idx_pe_s3_media ON post_enrichment USING GIN (s3_media_keys)")
    
    # B-Tree indexes
    op.create_index('idx_pe_vision_at', 'post_enrichment', ['vision_analyzed_at'])
    op.create_index('idx_pe_memes', 'post_enrichment', ['vision_is_meme'], postgresql_where=sa.text('vision_is_meme = true'))


def downgrade() -> None:
    # Удаляем indexes
    op.drop_index('idx_pe_memes', 'post_enrichment')
    op.drop_index('idx_pe_vision_at', 'post_enrichment')
    op.execute("DROP INDEX IF EXISTS idx_pe_s3_media")
    op.execute("DROP INDEX IF EXISTS idx_pe_vision_ctx")
    op.execute("DROP INDEX IF EXISTS idx_pe_vision_class")
    
    # Удаляем constraints
    op.drop_constraint('chk_vision_tokens_used', 'post_enrichment')
    op.drop_constraint('chk_vision_analysis_reason', 'post_enrichment')
    op.drop_constraint('chk_vision_provider', 'post_enrichment')
    
    # Удаляем колонки из post_enrichment
    op.drop_column('post_enrichment', 's3_crawl_keys')
    op.drop_column('post_enrichment', 's3_vision_keys')
    op.drop_column('post_enrichment', 's3_media_keys')
    op.drop_column('post_enrichment', 'vision_analysis_reason')
    op.drop_column('post_enrichment', 'vision_cost_microunits')
    op.drop_column('post_enrichment', 'vision_tokens_used')
    op.drop_column('post_enrichment', 'vision_file_id')
    op.drop_column('post_enrichment', 'vision_analyzed_at')
    op.drop_column('post_enrichment', 'vision_model')
    op.drop_column('post_enrichment', 'vision_provider')
    op.drop_column('post_enrichment', 'vision_context')
    op.drop_column('post_enrichment', 'vision_is_meme')
    op.drop_column('post_enrichment', 'vision_ocr_text')
    op.drop_column('post_enrichment', 'vision_description')
    op.drop_column('post_enrichment', 'vision_classification')
    op.drop_column('post_enrichment', 'summary')
    
    # Удаляем post_media_map
    op.drop_index('idx_pmm_post', 'post_media_map')
    op.drop_index('idx_pmm_sha', 'post_media_map')
    op.drop_table('post_media_map')
    
    # Удаляем media_objects
    op.drop_index('idx_media_last_seen', 'media_objects')
    op.drop_index('idx_media_refs', 'media_objects')
    op.drop_index('idx_media_size', 'media_objects')
    op.drop_index('idx_media_mime', 'media_objects')
    op.drop_table('media_objects')

