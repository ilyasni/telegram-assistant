"""add engagement metrics to posts

Revision ID: 20250131_engagement_metrics
Revises: 20251104_user_version
Create Date: 2025-01-31 12:00:00.000000

Context7: Добавляем engagement_score как generated column для расчета engagement на основе views, reactions, forwards, replies.
Также добавляем индексы для оптимизации запросов по времени и каналам.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250131_engagement_metrics'
down_revision = '20251104_user_version'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Добавление engagement метрик в posts.
    
    Шаги:
    1. Проверяем существование колонок views_count, reactions_count, forwards_count, replies_count
    2. Если их нет - добавляем с default=0
    3. Добавляем engagement_score как GENERATED ALWAYS AS (...) STORED
    4. Добавляем индексы: (channel_id, posted_at DESC) и BRIN на posted_at
    """
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('posts')]
    
    # Проверяем и добавляем колонки engagement метрик, если их нет
    if 'views_count' not in existing_columns:
        op.add_column('posts', sa.Column('views_count', sa.Integer(), nullable=True, server_default='0'))
        op.alter_column('posts', 'views_count', nullable=False, server_default='0')
    
    if 'reactions_count' not in existing_columns:
        op.add_column('posts', sa.Column('reactions_count', sa.Integer(), nullable=True, server_default='0'))
        op.alter_column('posts', 'reactions_count', nullable=False, server_default='0')
    
    if 'forwards_count' not in existing_columns:
        op.add_column('posts', sa.Column('forwards_count', sa.Integer(), nullable=True, server_default='0'))
        op.alter_column('posts', 'forwards_count', nullable=False, server_default='0')
    
    if 'replies_count' not in existing_columns:
        op.add_column('posts', sa.Column('replies_count', sa.Integer(), nullable=True, server_default='0'))
        op.alter_column('posts', 'replies_count', nullable=False, server_default='0')
    
    # Добавляем engagement_score как GENERATED ALWAYS AS STORED
    # Формула: LOG(1 + views_count) + 2*LOG(1 + reactions_count) + 3*LOG(1 + forwards_count) + LOG(1 + replies_count)
    if 'engagement_score' not in existing_columns:
        op.execute("""
            ALTER TABLE posts 
            ADD COLUMN engagement_score REAL 
            GENERATED ALWAYS AS (
                LOG(1 + COALESCE(views_count, 0)) + 
                2 * LOG(1 + COALESCE(reactions_count, 0)) + 
                3 * LOG(1 + COALESCE(forwards_count, 0)) + 
                LOG(1 + COALESCE(replies_count, 0))
            ) STORED
        """)
    
    # Проверяем существование индексов
    result = conn.execute(sa.text("""
        SELECT indexname FROM pg_indexes 
        WHERE schemaname = 'public' AND tablename = 'posts'
    """))
    existing_index_names = [row[0] for row in result]
    
    # Индекс на (channel_id, posted_at DESC) для оптимизации запросов по каналам и времени
    if 'idx_posts_channel_posted_at' not in existing_index_names:
        op.create_index(
            'idx_posts_channel_posted_at',
            'posts',
            ['channel_id', sa.text('posted_at DESC')],
            postgresql_ops={'posted_at': 'DESC'}
        )
    
    # BRIN индекс на posted_at для сканирования временных окон
    if 'idx_posts_posted_at_brin' not in existing_index_names:
        op.execute("""
            CREATE INDEX idx_posts_posted_at_brin 
            ON posts USING BRIN (posted_at)
        """)


def downgrade() -> None:
    """
    Откат миграции: удаление engagement_score и индексов.
    ВАЖНО: Колонки views_count, reactions_count, forwards_count, replies_count НЕ удаляем,
    так как они могут использоваться в других местах.
    """
    
    # Удаляем индексы
    op.execute("DROP INDEX IF EXISTS idx_posts_posted_at_brin")
    op.drop_index('idx_posts_channel_posted_at', 'posts')
    
    # Удаляем engagement_score
    op.drop_column('posts', 'engagement_score')

