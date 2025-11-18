"""add user_feedback table

Context7 best practice: Создание таблицы для хранения feedback от пользователей с поддержкой статусов и multi-tenant изоляции.

Revision ID: 20251118_user_feedback
Revises: 20251117_remove_legacy
Create Date: 2025-11-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = '20251118_user_feedback'
down_revision = '20251117_remove_legacy'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Создание таблицы user_feedback для хранения комментариев и пожеланий пользователей.
    
    Таблица поддерживает:
    - Multi-tenant изоляцию через tenant_id
    - Статусы: pending, in_progress, resolved, closed
    - Админские заметки и информацию о том, кто решил feedback
    - Индексы для производительности запросов
    """
    op.create_table(
        'user_feedback',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('admin_notes', sa.Text(), nullable=True),
        sa.Column('resolved_by', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'resolved', 'closed')", name='ck_user_feedback_status'),
    )
    
    # Context7: Индексы для производительности запросов
    op.create_index('ix_user_feedback_user_id', 'user_feedback', ['user_id'])
    op.create_index('ix_user_feedback_tenant_id', 'user_feedback', ['tenant_id'])
    op.create_index('ix_user_feedback_status', 'user_feedback', ['status'])
    op.create_index('ix_user_feedback_created_at', 'user_feedback', ['created_at'])


def downgrade() -> None:
    """Удаление таблицы user_feedback и всех связанных индексов."""
    op.drop_index('ix_user_feedback_created_at', table_name='user_feedback')
    op.drop_index('ix_user_feedback_status', table_name='user_feedback')
    op.drop_index('ix_user_feedback_tenant_id', table_name='user_feedback')
    op.drop_index('ix_user_feedback_user_id', table_name='user_feedback')
    op.drop_table('user_feedback')

