"""add user_theme table for theme subscriptions

Revision ID: 20260113_add_user_theme
Revises: 20260113_add_channel_source
Create Date: 2026-01-13

Context7: Создание таблицы user_theme для отслеживания подключенных подборок у пользователей.
Позволяет администратору управлять составом подборок без влияния на пользователей.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260113_add_user_theme'
down_revision = '20260113_add_channel_source'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание таблицы user_theme."""
    
    # Проверяем существование таблицы themes
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'themes' not in existing_tables:
        # Если таблицы themes нет, создаём таблицу без внешнего ключа
        # (themes может быть в другой БД или создана позже)
        op.create_table(
            'user_theme',
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('theme_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('subscribed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.PrimaryKeyConstraint('user_id', 'theme_id', name='pk_user_theme'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        )
    else:
        # Если таблица themes существует, создаём с внешним ключом
        op.create_table(
            'user_theme',
            sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('theme_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('subscribed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.PrimaryKeyConstraint('user_id', 'theme_id', name='pk_user_theme'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['theme_id'], ['themes.id'], ondelete='CASCADE'),
        )
    
    # Индексы для производительности
    op.create_index(
        'ix_user_theme_user_active',
        'user_theme',
        ['user_id', 'is_active']
    )
    
    op.create_index(
        'ix_user_theme_theme_active',
        'user_theme',
        ['theme_id', 'is_active']
    )


def downgrade() -> None:
    """Удаление таблицы user_theme."""
    
    op.drop_index('ix_user_theme_theme_active', table_name='user_theme')
    op.drop_index('ix_user_theme_user_active', table_name='user_theme')
    op.drop_table('user_theme')
