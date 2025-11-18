"""add digest and trends tables

Revision ID: 20250131_digest_trends
Revises: 20250131_album_aggregates
Create Date: 2025-01-31 13:00:00.000000

Context7: Добавляем таблицы для дайджестов и трендов:
- digest_settings - настройки дайджестов пользователя
- digest_history - история отправленных дайджестов
- rag_query_history - история запросов пользователя для анализа намерений
- trends_detection - обнаруженные тренды (глобальные)
- trend_alerts - уведомления пользователей о трендах
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import logging

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision = '20250131_digest_trends'
down_revision = '20250131_album_aggregates'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Создание таблиц для дайджестов и трендов.
    """
    
    # ============================================================================
    # 1. digest_settings - настройки дайджестов пользователя
    # ============================================================================
    op.create_table(
        'digest_settings',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('schedule_time', sa.Time(), nullable=False, server_default='09:00:00'),
        sa.Column('schedule_tz', sa.String(255), nullable=False, server_default='Europe/Moscow'),
        sa.Column('frequency', sa.String(20), nullable=False, server_default='daily'),
        sa.Column('topics', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('channels_filter', postgresql.JSONB(), nullable=True),
        sa.Column('max_items_per_digest', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.CheckConstraint("frequency IN ('daily', 'weekly', 'monthly')", name='chk_digest_frequency'),
        sa.CheckConstraint('max_items_per_digest > 0', name='chk_digest_max_items')
    )
    
    # GIN индекс на topics для быстрого поиска
    op.execute("""
        CREATE INDEX idx_digest_settings_topics_gin 
        ON digest_settings USING GIN (topics)
    """)
    
    # Индекс на user_id (уже есть как PK, но для полноты)
    op.create_index('idx_digest_settings_user_id', 'digest_settings', ['user_id'])
    
    # ============================================================================
    # 2. digest_history - история отправленных дайджестов
    # ============================================================================
    op.create_table(
        'digest_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('digest_date', sa.Date(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('posts_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('topics', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name='chk_digest_status')
    )
    
    # Индексы для digest_history
    op.create_index('idx_digest_history_user_id', 'digest_history', ['user_id'])
    op.create_index('idx_digest_history_digest_date', 'digest_history', ['digest_date'])
    op.create_index('idx_digest_history_status', 'digest_history', ['status'])
    op.create_index('idx_digest_history_created_at', 'digest_history', [sa.text('created_at DESC')])
    
    # ============================================================================
    # 3. rag_query_history - история запросов пользователя для анализа намерений
    # ============================================================================
    op.create_table(
        'rag_query_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('query_type', sa.String(50), nullable=False),
        sa.Column('intent', sa.String(50), nullable=True),
        sa.Column('confidence', sa.REAL(), nullable=True),
        sa.Column('response_text', sa.Text(), nullable=True),
        sa.Column('sources_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('processing_time_ms', sa.Integer(), nullable=True),
        sa.Column('audio_file_id', sa.String(255), nullable=True),
        sa.Column('transcription_text', sa.Text(), nullable=True),
        sa.Column('transcription_provider', sa.String(50), nullable=False, server_default='salutespeech'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.CheckConstraint("query_type IN ('ask', 'search', 'recommend', 'trend', 'digest')", name='chk_rag_query_type'),
        sa.CheckConstraint('confidence >= 0.0 AND confidence <= 1.0', name='chk_rag_confidence')
    )
    
    # Индексы для rag_query_history
    op.create_index('idx_rag_query_history_user_id', 'rag_query_history', ['user_id'])
    op.create_index('idx_rag_query_history_created_at', 'rag_query_history', [sa.text('created_at DESC')])
    op.create_index('idx_rag_query_history_intent', 'rag_query_history', ['intent'])
    op.create_index('idx_rag_query_history_query_type', 'rag_query_history', ['query_type'])
    
    # ============================================================================
    # 4. trends_detection - обнаруженные тренды (глобальные)
    # ============================================================================
    # Context7: Проверяем наличие расширения pgvector для vector типа
    # В self-hosted Supabase расширение может быть уже установлено или требуются права
    # Используем savepoint для изоляции ошибок создания расширения
    conn = op.get_bind()
    vector_extension_exists = False
    
    # Проверяем наличие расширения в отдельном savepoint
    savepoint_name = "check_vector_extension"
    try:
        # Context7: Используем savepoint для изоляции ошибок
        conn.execute(sa.text(f"SAVEPOINT {savepoint_name}"))
        result = conn.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        vector_extension_exists = result.fetchone() is not None
        conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}"))
    except Exception:
        # Откатываемся к savepoint если ошибка
        try:
            conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
        except Exception:
            pass
        vector_extension_exists = False
    
    # Пытаемся создать расширение только если его нет (в отдельном savepoint)
    if not vector_extension_exists:
        try:
            savepoint_name = "create_vector_extension"
            conn.execute(sa.text(f"SAVEPOINT {savepoint_name}"))
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}"))
            vector_extension_exists = True
        except Exception as e:
            # Откатываемся к savepoint и продолжаем без vector
            try:
                conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
            except Exception:
                pass
            logger.warning(f"Could not create vector extension: {e}. Continuing without vector column.")
            vector_extension_exists = False
    
    # Создаем таблицу без trend_embedding сначала
    op.create_table(
        'trends_detection',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('trend_keyword', sa.String(500), nullable=False),
        sa.Column('frequency_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('growth_rate', sa.REAL(), nullable=True),
        sa.Column('engagement_score', sa.REAL(), nullable=True),
        sa.Column('first_mentioned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_mentioned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('channels_affected', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('posts_sample', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.CheckConstraint("status IN ('active', 'archived')", name='chk_trend_status'),
        sa.CheckConstraint('frequency_count >= 0', name='chk_trend_frequency')
    )
    
    # Добавляем trend_embedding как vector(1536) только если расширение доступно
    # Context7: Если расширение недоступно, используем JSONB как fallback
    if vector_extension_exists:
        try:
            op.execute("""
                ALTER TABLE trends_detection 
                ADD COLUMN trend_embedding vector(1536)
            """)
        except Exception as e:
            logger.warning(f"Could not add vector column: {e}. Using JSONB fallback.")
            # Fallback: используем JSONB для хранения embedding
            op.add_column('trends_detection', sa.Column('trend_embedding', postgresql.JSONB(), nullable=True))
    else:
        # Fallback: используем JSONB для хранения embedding
        op.add_column('trends_detection', sa.Column('trend_embedding', postgresql.JSONB(), nullable=True))
    
    # Индексы для trends_detection
    op.create_index('idx_trends_detection_keyword', 'trends_detection', ['trend_keyword'])
    op.create_index('idx_trends_detection_last_mentioned', 'trends_detection', [sa.text('last_mentioned_at DESC')])
    op.create_index('idx_trends_detection_detected_at', 'trends_detection', [sa.text('detected_at DESC')])
    op.create_index('idx_trends_detection_status', 'trends_detection', ['status'])
    op.create_index('idx_trends_detection_engagement', 'trends_detection', [sa.text('engagement_score DESC NULLS LAST')])
    
    # ============================================================================
    # 5. trend_alerts - уведомления пользователей о трендах
    # ============================================================================
    op.create_table(
        'trend_alerts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('trend_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trend_id'], ['trends_detection.id'], ondelete='CASCADE')
    )
    
    # Индексы для trend_alerts
    op.create_index('idx_trend_alerts_user_id', 'trend_alerts', ['user_id'])
    op.create_index('idx_trend_alerts_trend_id', 'trend_alerts', ['trend_id'])
    op.create_index('idx_trend_alerts_sent_at', 'trend_alerts', [sa.text('sent_at DESC')])
    
    # ============================================================================
    # 6. Обновление media_group_items (если таблица существует)
    # ============================================================================
    # Проверяем существование таблицы media_group_items
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    
    if 'media_group_items' in tables:
        existing_columns = [col['name'] for col in inspector.get_columns('media_group_items')]
        
        # Проверяем существование constraints
        result = conn.execute(sa.text("""
            SELECT conname FROM pg_constraint 
            WHERE conrelid = 'media_group_items'::regclass 
            AND contype = 'u'
        """))
        existing_unique_constraints = [row[0] for row in result]
        
        # Добавляем UNIQUE constraint (post_id, media_id) если есть media_id
        if 'media_id' in existing_columns and 'ux_media_group_items_post_media' not in existing_unique_constraints:
            op.create_unique_constraint(
                'ux_media_group_items_post_media',
                'media_group_items',
                ['post_id', 'media_id']
            )
        
        # Проверяем существование индексов
        result = conn.execute(sa.text("""
            SELECT indexname FROM pg_indexes 
            WHERE schemaname = 'public' AND tablename = 'media_group_items'
        """))
        existing_index_names = [row[0] for row in result]
        
        # Индекс на (post_id, position) для сортировки элементов альбома
        if 'idx_media_group_items_post_position' not in existing_index_names:
            op.create_index(
                'idx_media_group_items_post_position',
                'media_group_items',
                ['post_id', 'position']
            )


def downgrade() -> None:
    """
    Откат миграции: удаление всех таблиц и constraints.
    """
    
    # Context7: Проверяем существование constraint перед удалением (исправление асимметрии)
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT conname FROM pg_constraint 
        WHERE conrelid = 'media_group_items'::regclass 
        AND contype = 'u'
        AND conname = 'ux_media_group_items_post_media'
    """))
    existing_constraint = result.fetchone()
    
    # Удаляем индексы и constraints для media_group_items
    op.execute("DROP INDEX IF EXISTS idx_media_group_items_post_position")
    if existing_constraint:
        op.drop_constraint('ux_media_group_items_post_media', 'media_group_items', type_='unique')
    
    # Удаляем таблицы
    op.drop_table('trend_alerts')
    op.drop_table('trends_detection')
    op.drop_table('rag_query_history')
    op.drop_table('digest_history')
    op.drop_table('digest_settings')

