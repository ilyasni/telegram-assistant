"""add resolve fields to channels for exponential backoff and status tracking

Revision ID: 20260114_add_resolve_attempts
Revises: 20260113_add_user_theme
Create Date: 2026-01-14

Context7: Добавление полей resolve_status, resolve_attempts, last_resolve_at и last_resolve_error
в channels для реализации экспоненциального backoff и отслеживания статуса резолва каналов.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260114_add_resolve_attempts'
down_revision = '20260113_add_user_theme'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление полей resolve_status, resolve_attempts, last_resolve_at и last_resolve_error в channels."""
    
    # 1. Добавить колонку resolve_status (nullable)
    # Значения: 'ok', 'needs_id', 'needs_access', 'not_found', 'no_access', 'floodwait', 'invalid_username'
    op.add_column(
        'channels',
        sa.Column('resolve_status', sa.String(length=50), nullable=True)
    )
    
    # 2. Добавить колонку resolve_attempts с default=0
    op.add_column(
        'channels',
        sa.Column('resolve_attempts', sa.Integer(), nullable=False, server_default='0')
    )
    
    # 3. Добавить колонку last_resolve_at (nullable)
    op.add_column(
        'channels',
        sa.Column('last_resolve_at', sa.DateTime(timezone=True), nullable=True)
    )
    
    # 4. Добавить колонку last_resolve_error (nullable)
    op.add_column(
        'channels',
        sa.Column('last_resolve_error', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    """Откат изменений в channels."""
    
    # Удалить колонки
    op.drop_column('channels', 'last_resolve_error')
    op.drop_column('channels', 'last_resolve_at')
    op.drop_column('channels', 'resolve_attempts')
    op.drop_column('channels', 'resolve_status')
