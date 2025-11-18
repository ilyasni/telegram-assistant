"""add source field for P3 Sideloading (channel/group/dm/persona)

Revision ID: 20250121_add_source_field
Revises: 20251116_trend_agents
Create Date: 2025-01-21 12:00:00.000000

Context7 P3: Добавляем поле source в таблицы posts и group_messages
для различения источников сообщений (channel/group/dm/persona).
Это позволяет использовать существующие таблицы для sideloading личных диалогов.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250121_add_source_field'
down_revision = '20250202_install_pgvector'  # Context7: Исправлено - последняя миграция до ноября 2025
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7 P3: Добавление поля source в posts и group_messages.
    
    Шаги:
    1. Добавляем колонку source как nullable с default значением
    2. Backfill существующих записей (posts -> 'channel', group_messages -> 'group')
    3. Устанавливаем CHECK constraint для валидации значений
    4. Создаём индекс на source для быстрой фильтрации
    """
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # ============================================================================
    # ШАГ 1: Добавление source в posts
    # ============================================================================
    
    existing_posts_columns = [col['name'] for col in inspector.get_columns('posts')]
    
    if 'source' not in existing_posts_columns:
        # Добавляем колонку как nullable с default
        op.add_column('posts', sa.Column('source', sa.String(20), nullable=True, server_default='channel'))
        
        # Backfill существующих записей
        op.execute("""
            UPDATE posts 
            SET source = 'channel' 
            WHERE source IS NULL
        """)
        
        # Устанавливаем NOT NULL (после backfill)
        op.alter_column('posts', 'source', nullable=False, server_default='channel')
        
        # CHECK constraint для валидации значений
        op.execute("""
            ALTER TABLE posts 
            ADD CONSTRAINT chk_posts_source 
            CHECK (source IN ('channel', 'group', 'dm', 'persona'))
        """)
        
        # Индекс на source для быстрой фильтрации
        op.create_index('idx_posts_source', 'posts', ['source'])
    
    # ============================================================================
    # ШАГ 2: Добавление source в group_messages
    # ============================================================================
    
    existing_group_messages_columns = [col['name'] for col in inspector.get_columns('group_messages')]
    
    if 'source' not in existing_group_messages_columns:
        # Добавляем колонку как nullable с default
        op.add_column('group_messages', sa.Column('source', sa.String(20), nullable=True, server_default='group'))
        
        # Backfill существующих записей
        op.execute("""
            UPDATE group_messages 
            SET source = 'group' 
            WHERE source IS NULL
        """)
        
        # Устанавливаем NOT NULL (после backfill)
        op.alter_column('group_messages', 'source', nullable=False, server_default='group')
        
        # CHECK constraint для валидации значений
        op.execute("""
            ALTER TABLE group_messages 
            ADD CONSTRAINT chk_group_messages_source 
            CHECK (source IN ('group', 'dm', 'persona'))
        """)
        
        # Индекс на source для быстрой фильтрации
        op.create_index('idx_group_messages_source', 'group_messages', ['source'])


def downgrade() -> None:
    """
    Откат миграции: удаление поля source из posts и group_messages.
    """
    
    # Удаляем индексы
    op.execute("DROP INDEX IF EXISTS idx_posts_source")
    op.execute("DROP INDEX IF EXISTS idx_group_messages_source")
    
    # Удаляем CHECK constraints
    op.execute("ALTER TABLE posts DROP CONSTRAINT IF EXISTS chk_posts_source")
    op.execute("ALTER TABLE group_messages DROP CONSTRAINT IF EXISTS chk_group_messages_source")
    
    # Удаляем колонки
    op.drop_column('posts', 'source')
    op.drop_column('group_messages', 'source')

