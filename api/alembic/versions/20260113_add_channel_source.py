"""add source and theme_id fields to user_channel for theme subscriptions

Revision ID: 20260113_add_channel_source
Revises: 20251205_ocr_dictionaries
Create Date: 2026-01-13

Context7: Добавление полей source, theme_id и updated_at в user_channel для разделения
ручных каналов и каналов из подборок. Защита от гонок через частичные уникальные индексы.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260113_add_channel_source'
down_revision = '20251205_ocr_dictionaries'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление полей source, theme_id и updated_at в user_channel."""
    
    # 1. Добавить колонку source с default='manual'
    op.add_column(
        'user_channel',
        sa.Column('source', sa.String(length=20), nullable=False, server_default='manual')
    )
    
    # 2. Добавить колонку theme_id (nullable)
    op.add_column(
        'user_channel',
        sa.Column('theme_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    
    # 3. Добавить колонку updated_at (если её ещё нет)
    # Проверяем существование колонки через inspect
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('user_channel')]
    
    if 'updated_at' not in existing_columns:
        op.add_column(
            'user_channel',
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now()
            )
        )
    
    # 4. Backfill существующих записей: source='manual', theme_id=NULL
    op.execute("""
        UPDATE user_channel 
        SET source = 'manual', theme_id = NULL
        WHERE source IS NULL OR theme_id IS NOT NULL
    """)
    
    # 5. Добавить CHECK constraint для source
    op.create_check_constraint(
        'chk_user_channel_source',
        'user_channel',
        "source IN ('manual', 'theme')"
    )
    
    # 6. Добавить CHECK constraint для связи source и theme_id
    op.create_check_constraint(
        'chk_user_channel_source_theme_id',
        'user_channel',
        "(source = 'theme' AND theme_id IS NOT NULL) OR (source = 'manual' AND theme_id IS NULL)"
    )
    
    # 7. Добавить внешний ключ на themes.id (если таблица themes существует)
    # Проверяем существование таблицы themes
    existing_tables = inspector.get_table_names()
    if 'themes' in existing_tables:
        op.create_foreign_key(
            'fk_user_channel_theme_id',
            'user_channel',
            'themes',
            ['theme_id'],
            ['id'],
            ondelete='SET NULL'
        )
    
    # 8. Частичные уникальные индексы для защиты от гонок
    op.create_index(
        'uq_user_channel_manual',
        'user_channel',
        ['user_id', 'channel_id'],
        unique=True,
        postgresql_where=sa.text("source = 'manual'")
    )
    
    op.create_index(
        'uq_user_channel_theme',
        'user_channel',
        ['user_id', 'channel_id', 'theme_id'],
        unique=True,
        postgresql_where=sa.text("source = 'theme'")
    )
    
    # 9. Индексы для производительности
    op.create_index(
        'ix_user_channel_user_source_active',
        'user_channel',
        ['user_id', 'source', 'is_active']
    )
    
    op.create_index(
        'ix_user_channel_user_channel_active',
        'user_channel',
        ['user_id', 'channel_id', 'is_active']
    )
    
    op.create_index(
        'ix_user_channel_theme_active',
        'user_channel',
        ['theme_id', 'is_active'],
        postgresql_where=sa.text("theme_id IS NOT NULL")
    )


def downgrade() -> None:
    """Откат изменений в user_channel."""
    
    # Удалить индексы
    op.drop_index('ix_user_channel_theme_active', table_name='user_channel')
    op.drop_index('ix_user_channel_user_channel_active', table_name='user_channel')
    op.drop_index('ix_user_channel_user_source_active', table_name='user_channel')
    op.drop_index('uq_user_channel_theme', table_name='user_channel')
    op.drop_index('uq_user_channel_manual', table_name='user_channel')
    
    # Удалить внешний ключ
    op.drop_constraint('fk_user_channel_theme_id', 'user_channel', type_='foreignkey')
    
    # Удалить CHECK constraints
    op.drop_constraint('chk_user_channel_source_theme_id', 'user_channel', type_='check')
    op.drop_constraint('chk_user_channel_source', 'user_channel', type_='check')
    
    # Удалить колонки
    op.drop_column('user_channel', 'updated_at')
    op.drop_column('user_channel', 'theme_id')
    op.drop_column('user_channel', 'source')
