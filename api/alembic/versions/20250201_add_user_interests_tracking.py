"""add user interests tracking

Revision ID: 20250201_user_interests
Revises: 20250131_digest_trends
Create Date: 2025-02-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250201_user_interests'
down_revision = '20250131_digest_trends'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Создание таблицы user_interests для хранения интересов пользователей.
    
    Гибридный подход:
    - PostgreSQL: для быстрых запросов и аналитики
    - Neo4j: для рекомендаций через графовые связи (синхронизируется периодически)
    - Redis: для быстрых обновлений в реальном времени (кэш)
    """
    # Создание таблицы user_interests
    op.create_table(
        'user_interests',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('topic', sa.Text(), nullable=False),
        sa.Column('weight', sa.REAL(), nullable=False, server_default='0.0'),
        sa.Column('query_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_updated', postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'topic')
    )
    
    # Context7: Индексы для быстрого поиска
    op.create_index(
        'idx_user_interests_user_id',
        'user_interests',
        ['user_id']
    )
    
    op.create_index(
        'idx_user_interests_weight',
        'user_interests',
        ['user_id', sa.text('weight DESC')]
    )
    
    op.create_index(
        'idx_user_interests_topic',
        'user_interests',
        ['topic']
    )
    
    # Context7: Индекс для быстрого поиска по времени обновления (для синхронизации)
    op.create_index(
        'idx_user_interests_last_updated',
        'user_interests',
        ['last_updated'],
        postgresql_ops={'last_updated': 'DESC'}
    )


def downgrade() -> None:
    """Откат миграции."""
    op.drop_index('idx_user_interests_last_updated', table_name='user_interests')
    op.drop_index('idx_user_interests_topic', table_name='user_interests')
    op.drop_index('idx_user_interests_weight', table_name='user_interests')
    op.drop_index('idx_user_interests_user_id', table_name='user_interests')
    op.drop_table('user_interests')

