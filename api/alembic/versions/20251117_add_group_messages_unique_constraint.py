"""add group_messages unique constraint

Context7 best practice: Добавление UNIQUE constraint на (group_id, tg_message_id) для поддержки ON CONFLICT в telegram_client.py

Revision ID: 20251117_group_msg_unique
Revises: 20251116_trend_agents
Create Date: 2025-11-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20251117_group_msg_unique'  # Context7: Сокращен для VARCHAR(32) в alembic_version
down_revision = '20251116_trend_agents'  # Context7: Исправлено на правильный revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Добавление UNIQUE constraint на (group_id, tg_message_id) для поддержки ON CONFLICT.
    
    Это исправляет ошибку "there is no unique or exclusion constraint matching the ON CONFLICT specification"
    в telegram_client.py при сохранении group messages.
    """
    # Context7: Проверяем существование constraint перед созданием
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT constraint_name 
        FROM information_schema.table_constraints 
        WHERE table_schema = 'public' 
        AND table_name = 'group_messages'
        AND constraint_type = 'UNIQUE'
        AND constraint_name LIKE '%group_id%tg_message_id%'
    """))
    existing_constraint = result.fetchone()
    
    if not existing_constraint:
        # Context7: Создаем UNIQUE constraint на (group_id, tg_message_id)
        op.create_unique_constraint(
            'ux_group_messages_group_tg_message',
            'group_messages',
            ['group_id', 'tg_message_id']
        )


def downgrade() -> None:
    """Удаление UNIQUE constraint."""
    op.drop_constraint('ux_group_messages_group_tg_message', 'group_messages', type_='unique')

